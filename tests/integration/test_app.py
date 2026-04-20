"""Integration tests for the FastAPI application structure.

Tests here verify that:
  - The application factory produces a runnable app.
  - All expected routes are registered.
  - The OpenAPI schema is available in debug mode.
"""



class TestAppStartup:
    def test_app_instance_is_created(self, app):
        """The create_app() factory must return a FastAPI application."""
        from fastapi import FastAPI

        assert isinstance(app, FastAPI)

    def test_app_title(self, app):
        assert app.title == "Mindwall"

    def test_app_version(self, app):
        assert app.version == "0.1.0"


class TestRouteRegistration:
    def test_home_route_exists(self, client):
        response = client.get("/")
        # Unauthenticated: should render landing page (200), not 404
        assert response.status_code == 200

    def test_login_page_exists(self, client):
        response = client.get("/login")
        assert response.status_code == 200

    def test_health_live_route_exists(self, client):
        response = client.get("/health/live")
        assert response.status_code == 200

    def test_health_ready_route_exists(self, client):
        # May return 200 or 503 depending on test environment services.
        response = client.get("/health/ready")
        assert response.status_code in (200, 503)

    def test_admin_requires_auth(self, client):
        """Admin route must return 401 for unauthenticated requests."""
        response = client.get("/admin/", follow_redirects=False)
        assert response.status_code in (401, 403)

    def test_unknown_route_returns_404(self, client):
        response = client.get("/this-route-does-not-exist")
        assert response.status_code == 404

    def test_openapi_schema_available_in_debug(self, app, client):
        """In debug mode the OpenAPI schema endpoint must be accessible."""
        # Our test settings have DEBUG=true, so docs should be available.
        response = client.get("/api/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert data["info"]["title"] == "Mindwall"
