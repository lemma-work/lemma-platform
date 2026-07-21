from app.core.email.transactional import (
    EmailAction,
    EmailDetail,
    render_transactional_email,
)


def test_transactional_email_has_shared_brand_and_plain_text_parity():
    rendered = render_transactional_email(
        preheader="A concise preview",
        eyebrow="Account security",
        heading="Verify your email",
        body=("Confirm your address.",),
        action=EmailAction(
            "Verify email",
            "https://lemma.work/auth/verify-email?token=abc&tenantId=public",
        ),
        details=(EmailDetail("Workspace", "support_app"),),
        highlights=("One secure Lemma account",),
        footer=("Ignore this message if you did not request it.",),
    )

    assert "A concise preview" in rendered.html
    assert "background:#f5f5f3" in rendered.html
    assert "Lemma" in rendered.html
    assert "Button not working?" in rendered.html
    assert (
        "https://lemma.work/auth/verify-email?token=abc&amp;tenantId=public"
        in rendered.html
    )
    assert (
        "Verify email: https://lemma.work/auth/verify-email?token=abc&tenantId=public"
        in rendered.text
    )
    assert "Workspace: support_app" in rendered.text


def test_transactional_email_escapes_every_dynamic_value():
    rendered = render_transactional_email(
        preheader='<img src=x onerror="bad">',
        eyebrow="Security",
        heading="<script>alert(1)</script>",
        body=("Hello <Admin>",),
        action=EmailAction("Click <now>", 'https://example.com/?q="unsafe"&x=1'),
        details=(EmailDetail("Pod", "support<script>"),),
    )

    assert "<script>" not in rendered.html
    assert "<Admin>" not in rendered.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered.html
    assert "Hello &lt;Admin&gt;" in rendered.html
    assert "support&lt;script&gt;" in rendered.html
    assert "q=&quot;unsafe&quot;&amp;x=1" in rendered.html
