import pytest
from httpx import AsyncClient
from fastapi import status

pytestmark = pytest.mark.asyncio

class TestAuthentication:
    async def test_register_user(
        self,
        client: AsyncClient,
        test_user_data: dict
    ):
        """Test user registration."""
        response = await client.post(
            "/api/v1/auth/register",
            json=test_user_data
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["email"] == test_user_data["email"]
        assert "id" in data
        assert "password" not in data

    async def test_login_user(
        self,
        client: AsyncClient,
        test_user_data: dict
    ):
        """Test user login."""
        # First register the user
        await client.post(
            "/api/v1/auth/register",
            json=test_user_data
        )
        
        # Then try to login
        response = await client.post(
            "/api/v1/auth/login",
            data={
                "username": test_user_data["email"],
                "password": test_user_data["password"]
            }
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(
        self,
        client: AsyncClient,
        test_user_data: dict
    ):
        """Test login with wrong password."""
        # First register the user
        await client.post(
            "/api/v1/auth/register",
            json=test_user_data
        )
        
        # Then try to login with wrong password
        response = await client.post(
            "/api/v1/auth/login",
            data={
                "username": test_user_data["email"],
                "password": "wrongpassword"
            }
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_get_current_user(
        self,
        client: AsyncClient,
        test_user_data: dict
    ):
        """Test getting current user details."""
        # Register user
        await client.post(
            "/api/v1/auth/register",
            json=test_user_data
        )
        
        # Login to get token
        login_response = await client.post(
            "/api/v1/auth/login",
            data={
                "username": test_user_data["email"],
                "password": test_user_data["password"]
            }
        )
        token = login_response.json()["access_token"]
        
        # Get current user details
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["email"] == test_user_data["email"]
        assert "password" not in data 