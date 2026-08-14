from app.core.net.aiohttp_client import new_aiohttp_session


async def get_atlassian_cloud_id(access_token: str) -> str:
    url = "https://api.atlassian.com/oauth/token/accessible-resources"
    headers = {
        "content-type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    async with new_aiohttp_session() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                return (await response.json())[0]["id"]
            else:
                raise Exception(f"Error getting Atlassian Cloud ID: {response.status}")
