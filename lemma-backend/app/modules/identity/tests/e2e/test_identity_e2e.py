from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
import pytest
from supertokens_python.recipe.thirdparty.providers import config_utils
from supertokens_python.recipe.thirdparty.providers.custom import GenericProvider
from supertokens_python.recipe.thirdparty.types import (
    RawUserInfoFromProvider,
    UserInfo,
    UserInfoEmail,
)

pytestmark = pytest.mark.e2e


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _emailpassword_payload(email: str, password: str) -> dict:
    return {
        "formFields": [
            {"id": "email", "value": email},
            {"id": "password", "value": password},
        ]
    }


@pytest.fixture
def mock_google_provider(monkeypatch):
    async def _discover_oidc_endpoints(config):
        return config

    async def _get_user_info(self, oauth_tokens, user_context):
        email = oauth_tokens["testEmail"]
        third_party_user_id = oauth_tokens["testThirdPartyUserId"]
        return UserInfo(
            third_party_user_id=third_party_user_id,
            email=UserInfoEmail(email, True),
            raw_user_info_from_provider=RawUserInfoFromProvider(
                from_id_token_payload={
                    "sub": third_party_user_id,
                    "email": email,
                    "email_verified": True,
                },
                from_user_info_api=None,
            ),
        )

    monkeypatch.setattr(config_utils, "discover_oidc_endpoints", _discover_oidc_endpoints)
    monkeypatch.setattr(GenericProvider, "get_user_info", _get_user_info)


@pytest.fixture
def google_signinup(async_client: AsyncClient):
    async def _google_signinup(
        email: str,
        *,
        third_party_user_id: str = "google-user-1",
    ):
        return await async_client.post(
            "/st/auth/signinup",
            json={
                "thirdPartyId": "google",
                "oAuthTokens": {
                    "testEmail": email,
                    "testThirdPartyUserId": third_party_user_id,
                },
            },
        )

    return _google_signinup


@pytest.mark.asyncio
async def test_user_me_and_profile_lifecycle(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    headers = _auth_headers(owner["token"])

    me_resp = await async_client.get("/users/me", headers=headers)
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["email"] == owner["email"]

    profile_before_resp = await async_client.get("/users/me/profile", headers=headers)
    assert profile_before_resp.status_code == 200

    profile_payload = {
        "first_name": "Anukul",
        "last_name": "Test",
        "mobile_number": "+1234567890",
        "country": "US",
        "timezone": "UTC",
        "date_of_birth": "1990-01-01",
    }
    upsert_resp = await async_client.post(
        "/users/me/profile",
        headers=headers,
        json=profile_payload,
    )
    assert upsert_resp.status_code == 201

    profile_after_resp = await async_client.get("/users/me/profile", headers=headers)
    assert profile_after_resp.status_code == 200
    profile_data = profile_after_resp.json()
    assert profile_data["first_name"] == "Anukul"
    assert profile_data["last_name"] == "Test"


@pytest.mark.asyncio
async def test_signup_does_not_create_personal_org(
    async_client: AsyncClient,
    signup_user,
):
    user = await signup_user(email=f"test+no-personal-{uuid4().hex[:8]}@gmail.com")
    headers = _auth_headers(user["token"])

    list_org_resp = await async_client.get("/organizations", headers=headers)
    assert list_org_resp.status_code == 200
    assert list_org_resp.json()["items"] == []


@pytest.mark.asyncio
async def test_org_domain_slug_availability_and_suggestions(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user(email=f"owner-{uuid4().hex[:8]}@acme-example.com")
    coworker = await signup_user(email=f"teammate-{uuid4().hex[:8]}@acme-example.com")
    outsider = await signup_user(email=f"outsider-{uuid4().hex[:8]}@other-example.com")
    gmail_user = await signup_user(email=f"personal-{uuid4().hex[:8]}@gmail.com")

    owner_headers = _auth_headers(owner["token"])

    available_before_resp = await async_client.get(
        "/organizations/slug-availability",
        headers=owner_headers,
        params={"slug": "Acme Auto Join"},
    )
    assert available_before_resp.status_code == 200
    assert available_before_resp.json() == {
        "slug": "acme-auto-join",
        "available": True,
    }

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": "Acme Auto Join", "join_policy": "EMAIL_DOMAIN"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org = create_org_resp.json()
    assert org["slug"] == "acme-auto-join"
    assert org["email_domain"] == "acme-example.com"
    assert org["join_policy"] == "EMAIL_DOMAIN"

    available_after_resp = await async_client.get(
        "/organizations/slug-availability",
        headers=owner_headers,
        params={"slug": "acme-auto-join"},
    )
    assert available_after_resp.status_code == 200
    assert available_after_resp.json()["available"] is False

    coworker_suggestions_resp = await async_client.get(
        "/organizations/suggested",
        headers=_auth_headers(coworker["token"]),
    )
    assert coworker_suggestions_resp.status_code == 200
    assert [item["id"] for item in coworker_suggestions_resp.json()["items"]] == [
        org["id"]
    ]

    outsider_suggestions_resp = await async_client.get(
        "/organizations/suggested",
        headers=_auth_headers(outsider["token"]),
    )
    assert outsider_suggestions_resp.status_code == 200
    assert outsider_suggestions_resp.json()["items"] == []

    # A second same-domain user cannot claim the domain for EMAIL_DOMAIN...
    duplicate_domain_resp = await async_client.post(
        "/organizations",
        headers=_auth_headers(coworker["token"]),
        json={"name": "Acme Duplicate Domain", "join_policy": "EMAIL_DOMAIN"},
    )
    assert duplicate_domain_resp.status_code == 409
    assert "email domain" in duplicate_domain_resp.json()["message"].lower()

    # ...but can still create their own org with the default INVITE_ONLY policy.
    coworker_org_resp = await async_client.post(
        "/organizations",
        headers=_auth_headers(coworker["token"]),
        json={"name": "Acme Coworker Org"},
    )
    assert coworker_org_resp.status_code == 201, coworker_org_resp.text
    assert coworker_org_resp.json()["join_policy"] == "INVITE_ONLY"
    assert coworker_org_resp.json()["email_domain"] is None

    # Personal email domains are not eligible for the EMAIL_DOMAIN policy.
    gmail_domain_resp = await async_client.post(
        "/organizations",
        headers=_auth_headers(gmail_user["token"]),
        json={"name": f"Gmail Domain {uuid4().hex[:8]}", "join_policy": "EMAIL_DOMAIN"},
    )
    assert gmail_domain_resp.status_code == 400

    gmail_org_resp = await async_client.post(
        "/organizations",
        headers=_auth_headers(gmail_user["token"]),
        json={"name": f"Gmail Org {uuid4().hex[:8]}"},
    )
    assert gmail_org_resp.status_code == 201
    assert gmail_org_resp.json()["email_domain"] is None


@pytest.mark.asyncio
async def test_organization_slug_is_globally_unique(
    async_client: AsyncClient,
    signup_user,
):
    first_owner = await signup_user(email=f"slug-a-{uuid4().hex[:8]}@slug-a.example")
    second_owner = await signup_user(email=f"slug-b-{uuid4().hex[:8]}@slug-b.example")

    first = await async_client.post(
        "/organizations",
        headers=_auth_headers(first_owner["token"]),
        json={"name": "Global Slug Collision"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["slug"] == "global-slug-collision"

    second = await async_client.post(
        "/organizations",
        headers=_auth_headers(second_owner["token"]),
        json={"name": "Global-Slug Collision"},
    )
    assert second.status_code == 409, second.text
    assert "slug" in second.json()["message"].lower()


@pytest.mark.asyncio
async def test_organization_full_api_flow(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    invitee = await signup_user()
    third_user = await signup_user()

    owner_headers = _auth_headers(owner["token"])
    invitee_headers = _auth_headers(invitee["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": "Identity Refactor Org"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org = create_org_resp.json()
    org_id = org["id"]

    list_org_resp = await async_client.get("/organizations", headers=owner_headers)
    assert list_org_resp.status_code == 200
    assert any(item["id"] == org_id for item in list_org_resp.json()["items"])

    get_org_resp = await async_client.get(
        f"/organizations/{org_id}",
        headers=owner_headers,
    )
    assert get_org_resp.status_code == 200
    assert get_org_resp.json()["id"] == org_id

    members_resp = await async_client.get(
        f"/organizations/{org_id}/members",
        headers=owner_headers,
    )
    assert members_resp.status_code == 200
    assert len(members_resp.json()["items"]) == 1

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": invitee["email"], "role": "ORG_MEMBER"},
    )
    assert invite_resp.status_code == 201, invite_resp.text
    invitation = invite_resp.json()
    invitation_id = invitation["id"]
    assert invitation["status"] == "PENDING"
    assert invitation["expires_at"] is not None
    assert invitation["organization_name"] == "Identity Refactor Org"

    list_invites_resp = await async_client.get(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
    )
    assert list_invites_resp.status_code == 200
    assert any(item["id"] == invitation_id for item in list_invites_resp.json()["items"])

    list_my_invites_resp = await async_client.get(
        "/organizations/invitations",
        headers=invitee_headers,
    )
    assert list_my_invites_resp.status_code == 200
    listed_invitation = next(
        item
        for item in list_my_invites_resp.json()["items"]
        if item["id"] == invitation_id
    )
    assert listed_invitation["organization_name"] == "Identity Refactor Org"

    get_invite_resp = await async_client.get(
        f"/organizations/invitations/{invitation_id}",
        headers=owner_headers,
    )
    assert get_invite_resp.status_code == 200
    assert get_invite_resp.json()["id"] == invitation_id
    assert get_invite_resp.json()["organization_name"] == "Identity Refactor Org"

    accept_resp = await async_client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200, accept_resp.text

    get_accepted_invite_resp = await async_client.get(
        f"/organizations/invitations/{invitation_id}",
        headers=owner_headers,
    )
    assert get_accepted_invite_resp.status_code == 200
    assert get_accepted_invite_resp.json()["status"] == "ACCEPTED"

    members_after_accept_resp = await async_client.get(
        f"/organizations/{org_id}/members",
        headers=owner_headers,
    )
    assert members_after_accept_resp.status_code == 200
    members = members_after_accept_resp.json()["items"]
    invitee_member = next(
        member for member in members if member.get("user", {}).get("email") == invitee["email"]
    )

    update_role_resp = await async_client.patch(
        f"/organizations/{org_id}/members/{invitee_member['id']}/role",
        headers=owner_headers,
        json={"role": "ORG_EDITOR"},
    )
    assert update_role_resp.status_code == 200
    assert update_role_resp.json()["role"] == "ORG_EDITOR"

    remove_member_resp = await async_client.delete(
        f"/organizations/{org_id}/members/{invitee_member['id']}",
        headers=owner_headers,
    )
    assert remove_member_resp.status_code == 204

    invite_third_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": third_user["email"], "role": "ORG_MEMBER"},
    )
    assert invite_third_resp.status_code == 201
    third_invitation_id = invite_third_resp.json()["id"]

    revoke_resp = await async_client.delete(
        f"/organizations/invitations/{third_invitation_id}",
        headers=owner_headers,
    )
    assert revoke_resp.status_code == 204

    revoked_invite_resp = await async_client.get(
        f"/organizations/invitations/{third_invitation_id}",
        headers=owner_headers,
    )
    assert revoked_invite_resp.status_code == 200
    assert revoked_invite_resp.json()["status"] == "REVOKED"


@pytest.mark.asyncio
async def test_identity_error_translation_payload(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    outsider = await signup_user()
    invitee = await signup_user()

    owner_headers = _auth_headers(owner["token"])
    outsider_headers = _auth_headers(outsider["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": "Private Org"},
    )
    assert create_org_resp.status_code == 201
    org_id = create_org_resp.json()["id"]

    no_access_resp = await async_client.get(
        f"/organizations/{org_id}",
        headers=outsider_headers,
    )
    assert no_access_resp.status_code == 403
    no_access_payload = no_access_resp.json()
    assert no_access_payload["code"] == "IDENTITY_ACCESS_DENIED"
    assert "message" in no_access_payload

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": invitee["email"], "role": "ORG_MEMBER"},
    )
    assert invite_resp.status_code == 201
    invitation_id = invite_resp.json()["id"]

    mismatch_accept_resp = await async_client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers=outsider_headers,
    )
    assert mismatch_accept_resp.status_code == 403
    mismatch_payload = mismatch_accept_resp.json()
    assert mismatch_payload["code"] == "IDENTITY_ACCESS_DENIED"
    assert "not for your email" in mismatch_payload["message"].lower()


@pytest.mark.asyncio
async def test_google_signinup_is_blocked_for_existing_emailpassword_user(
    async_client: AsyncClient,
    signup_user,
    google_signinup,
    mock_google_provider,
):
    existing_user = await signup_user()

    response = await google_signinup(
        existing_user["email"],
        third_party_user_id="google-existing-emailpassword-conflict",
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "SIGN_IN_UP_NOT_ALLOWED",
        "reason": "This email is already registered with email and password. Please sign in using your password.",
    }


@pytest.mark.asyncio
async def test_email_signup_is_blocked_for_existing_google_user(
    async_client: AsyncClient,
    google_signinup,
    mock_google_provider,
):
    email = "test+google-signup-conflict@example.com"

    google_response = await google_signinup(
        email,
        third_party_user_id="google-signup-conflict",
    )
    google_payload = google_response.json()
    assert google_response.status_code == 200
    assert google_payload["status"] == "OK", google_payload

    emailpassword_response = await async_client.post(
        "/st/auth/signup",
        json=_emailpassword_payload(email, "TestPassword@123"),
    )

    assert emailpassword_response.status_code == 200
    assert emailpassword_response.json() == {
        "status": "SIGN_UP_NOT_ALLOWED",
        "reason": "This email is already registered with Google. Please sign in using Google.",
    }


@pytest.mark.asyncio
async def test_email_signin_is_blocked_for_existing_google_user(
    async_client: AsyncClient,
    google_signinup,
    mock_google_provider,
):
    email = "test+google-signin-conflict@example.com"

    google_response = await google_signinup(
        email,
        third_party_user_id="google-signin-conflict",
    )
    google_payload = google_response.json()
    assert google_response.status_code == 200
    assert google_payload["status"] == "OK", google_payload

    emailpassword_response = await async_client.post(
        "/st/auth/signin",
        json=_emailpassword_payload(email, "TestPassword@123"),
    )

    assert emailpassword_response.status_code == 200
    assert emailpassword_response.json() == {
        "status": "SIGN_IN_NOT_ALLOWED",
        "reason": "This email is already registered with Google. Please sign in using Google.",
    }


@pytest.mark.asyncio
async def test_existing_google_user_can_signinup_again(
    google_signinup,
    mock_google_provider,
):
    email = "test+google-repeat@example.com"

    first_response = await google_signinup(
        email,
        third_party_user_id="google-repeat-user",
    )
    first_payload = first_response.json()
    assert first_response.status_code == 200
    assert first_payload["status"] == "OK", first_payload
    assert first_payload["createdNewRecipeUser"] is True

    second_response = await google_signinup(
        email,
        third_party_user_id="google-repeat-user",
    )
    second_payload = second_response.json()
    assert second_response.status_code == 200
    assert second_payload["status"] == "OK", second_payload
    assert second_payload["createdNewRecipeUser"] is False


@pytest.mark.asyncio
async def test_invite_with_pod_id_adds_user_to_pod_on_accept(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    invitee = await signup_user()
    owner_headers = _auth_headers(owner["token"])
    invitee_headers = _auth_headers(invitee["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Pod Invite Org {uuid4().hex[:8]}"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org_id = create_org_resp.json()["id"]

    create_pod_resp = await async_client.post(
        "/pods",
        headers=owner_headers,
        json={
            "name": f"Pod Invite Pod {uuid4().hex[:8]}",
            "description": "Pod invitation description",
            "organization_id": org_id,
        },
    )
    assert create_pod_resp.status_code == 201, create_pod_resp.text
    pod_id = create_pod_resp.json()["id"]

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={
            "email": invitee["email"],
            "role": "ORG_MEMBER",
            "pod_id": pod_id,
            "pod_role": "POD_EDITOR",
            "redirect_uri": "https://app.example.com/invite/accepted",
        },
    )
    assert invite_resp.status_code == 201, invite_resp.text
    invitation = invite_resp.json()
    assert invitation["pod_id"] == pod_id
    assert invitation["pod_role"] == "POD_EDITOR"
    assert invitation["redirect_uri"] == "https://app.example.com/invite/accepted"
    assert invitation["organization_name"].startswith("Pod Invite Org")

    get_invite_resp = await async_client.get(
        f"/organizations/invitations/{invitation['id']}",
        headers=invitee_headers,
    )
    assert get_invite_resp.status_code == 200
    invite_detail = get_invite_resp.json()
    assert invite_detail["organization_name"] == invitation["organization_name"]
    assert invite_detail["pod_name"].startswith("Pod Invite Pod")
    assert invite_detail["pod_description"] == "Pod invitation description"
    assert invite_detail["redirect_uri"] == "https://app.example.com/invite/accepted"

    accept_resp = await async_client.post(
        f"/organizations/invitations/{invitation['id']}/accept",
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200, accept_resp.text
    assert (
        accept_resp.json()["redirect_uri"]
        == "https://app.example.com/invite/accepted"
    )

    members_resp = await async_client.get(
        f"/organizations/{org_id}/members",
        headers=owner_headers,
    )
    assert members_resp.status_code == 200
    members = members_resp.json()["items"]
    invitee_member = next(
        m for m in members if m.get("user", {}).get("email") == invitee["email"]
    )
    assert invitee_member is not None

    pod_members_resp = await async_client.get(
        f"/pods/{pod_id}/members",
        headers=owner_headers,
    )
    assert pod_members_resp.status_code == 200
    pod_members = pod_members_resp.json().get("items", [])
    invitee_pod_member = next(
        (m for m in pod_members if m["user_id"] == invitee_member["user"]["id"]),
        None,
    )
    assert invitee_pod_member is not None
    assert invitee_pod_member["roles"] == ["POD_EDITOR"]


@pytest.mark.asyncio
async def test_invite_with_pod_id_defaults_role_to_POD_USER(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    invitee = await signup_user()
    owner_headers = _auth_headers(owner["token"])
    invitee_headers = _auth_headers(invitee["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Pod Default Role Org {uuid4().hex[:8]}"},
    )
    assert create_org_resp.status_code == 201
    org_id = create_org_resp.json()["id"]

    create_pod_resp = await async_client.post(
        "/pods",
        headers=owner_headers,
        json={
            "name": f"Pod Default Role Pod {uuid4().hex[:8]}",
            "organization_id": org_id,
        },
    )
    assert create_pod_resp.status_code == 201
    pod_id = create_pod_resp.json()["id"]

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={
            "email": invitee["email"],
            "role": "ORG_MEMBER",
            "pod_id": pod_id,
        },
    )
    assert invite_resp.status_code == 201
    invitation = invite_resp.json()
    assert invitation["pod_id"] == pod_id
    assert invitation["pod_role"] is None

    accept_resp = await async_client.post(
        f"/organizations/invitations/{invitation['id']}/accept",
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200

    members_resp = await async_client.get(
        f"/organizations/{org_id}/members",
        headers=owner_headers,
    )
    assert members_resp.status_code == 200
    invitee_member = next(
        m
        for m in members_resp.json()["items"]
        if m.get("user", {}).get("email") == invitee["email"]
    )

    pod_members_resp = await async_client.get(
        f"/pods/{pod_id}/members",
        headers=owner_headers,
    )
    assert pod_members_resp.status_code == 200
    invitee_pod_member = next(
        m
        for m in pod_members_resp.json().get("items", [])
        if m["user_id"] == invitee_member["user"]["id"]
    )
    assert invitee_pod_member["roles"] == ["POD_USER"]


@pytest.mark.asyncio
async def test_invite_with_pod_id_from_different_org_is_rejected(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user(email=f"cross-org-owner-{uuid4().hex[:8]}@gmail.com")
    owner_headers = _auth_headers(owner["token"])

    create_org_a_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Org A {uuid4().hex[:8]}"},
    )
    assert create_org_a_resp.status_code == 201
    org_a_id = create_org_a_resp.json()["id"]

    create_org_b_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Org B {uuid4().hex[:8]}"},
    )
    assert create_org_b_resp.status_code == 201
    org_b_id = create_org_b_resp.json()["id"]

    create_pod_resp = await async_client.post(
        "/pods",
        headers=owner_headers,
        json={
            "name": f"Org B Pod {uuid4().hex[:8]}",
            "organization_id": org_b_id,
        },
    )
    assert create_pod_resp.status_code == 201
    pod_b_id = create_pod_resp.json()["id"]

    invite_resp = await async_client.post(
        f"/organizations/{org_a_id}/invitations",
        headers=owner_headers,
        json={
            "email": "test+cross-org-pod@example.com",
            "role": "ORG_MEMBER",
            "pod_id": pod_b_id,
        },
    )
    assert invite_resp.status_code == 400
    assert "Pod does not belong" in invite_resp.json()["message"]


@pytest.mark.asyncio
async def test_profile_mobile_and_telegram_uniqueness(
    async_client: AsyncClient,
    signup_user,
):
    first = await signup_user(email=f"first-{uuid4().hex[:8]}@uniq-example.com")
    second = await signup_user(email=f"second-{uuid4().hex[:8]}@uniq-example.com")
    first_headers = _auth_headers(first["token"])
    second_headers = _auth_headers(second["token"])

    set_first = await async_client.post(
        "/users/me/profile",
        headers=first_headers,
        json={"mobile_number": "+1 555 123 4567", "telegram_username": "AnukulT"},
    )
    assert set_first.status_code == 201

    # Same digits, different formatting -> conflict.
    dup_mobile = await async_client.post(
        "/users/me/profile",
        headers=second_headers,
        json={"mobile_number": "1(555)123-4567"},
    )
    assert dup_mobile.status_code == 409

    # Same telegram username, different case -> conflict.
    dup_telegram = await async_client.post(
        "/users/me/profile",
        headers=second_headers,
        json={"telegram_username": "anukult"},
    )
    assert dup_telegram.status_code == 409

    # Unique values are accepted.
    unique = await async_client.post(
        "/users/me/profile",
        headers=second_headers,
        json={"mobile_number": "+1 555 987 6543", "telegram_username": "someone_else"},
    )
    assert unique.status_code == 201

    # Re-saving one's own unchanged values is fine.
    resave = await async_client.post(
        "/users/me/profile",
        headers=first_headers,
        json={"mobile_number": "+1 555 123 4567", "telegram_username": "AnukulT"},
    )
    assert resave.status_code == 201


@pytest.mark.asyncio
async def test_org_public_join_and_policy_update(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user(email=f"owner-{uuid4().hex[:8]}@pubco-example.com")
    outsider = await signup_user(email=f"outsider-{uuid4().hex[:8]}@elsewhere-example.com")
    owner_headers = _auth_headers(owner["token"])
    outsider_headers = _auth_headers(outsider["token"])

    create = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"PubCo {uuid4().hex[:8]}"},
    )
    assert create.status_code == 201, create.text
    org = create.json()
    assert org["join_policy"] == "INVITE_ONLY"

    # Invite-only org rejects self-join.
    denied = await async_client.post(
        f"/organizations/{org['id']}/join", headers=outsider_headers
    )
    assert denied.status_code == 403

    # Owner opens the org to any Lemma user.
    patched = await async_client.patch(
        f"/organizations/{org['id']}",
        headers=owner_headers,
        json={"join_policy": "PUBLIC"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["join_policy"] == "PUBLIC"

    # Any user can now self-join.
    joined = await async_client.post(
        f"/organizations/{org['id']}/join", headers=outsider_headers
    )
    assert joined.status_code == 200

    # Non-owners cannot change the policy.
    forbidden = await async_client.patch(
        f"/organizations/{org['id']}",
        headers=outsider_headers,
        json={"join_policy": "INVITE_ONLY"},
    )
    assert forbidden.status_code == 403
