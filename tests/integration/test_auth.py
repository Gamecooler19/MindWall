"""Integration tests for auth routes (login/logout)."""

import pytest


class TestLoginPage:
    def test_get_login_returns_200(self, client):
        response = client.get("/login")
        assert response.status_code == 200

    def test_login_page_contains_form(self, client):
        response = client.get("/login")
        assert b"<form" in response.content
        assert b'action="/login"' in response.content

    def test_login_page_contains_email_field(self, client):
        response = client.get("/login")
        assert b'name="email"' in response.content

    def test_login_page_contains_password_field(self, client):
        response = client.get("/login")
        assert b'name="password"' in response.content


class TestLoginPost:
    def test_invalid_credentials_returns_401(self, client):
        response = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrong"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    def test_invalid_credentials_re_renders_form(self, client):
        response = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrong"},
        )
        # Should show the login form again with an error message
        assert b"<form" in response.content

    def test_invalid_credentials_shows_error(self, client):
        response = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrong"},
        )
        assert b"Invalid email or password" in response.content


class TestLogout:
    def test_logout_redirects_to_login(self, client):
        response = client.post("/logout", follow_redirects=False)
        assert response.status_code == 303
        assert "/login" in response.headers["location"]
