"""Integration tests for health check endpoints.

/health/live  — must always return 200 while the process is running.
/health/ready — returns 200 or 503 based on dependency availability.
              In the test environment, Redis is not guaranteed to be running,
              so we assert the correct response shape in both cases.
"""



class TestLivenessEndpoint:
    def test_returns_200(self, client):
        response = client.get("/health/live")
        assert response.status_code == 200

    def test_returns_ok_status(self, client):
        data = client.get("/health/live").json()
        assert data["status"] == "ok"

    def test_response_is_json(self, client):
        response = client.get("/health/live")
        assert "application/json" in response.headers["content-type"]


class TestReadinessEndpoint:
    def test_returns_json(self, client):
        response = client.get("/health/ready")
        assert "application/json" in response.headers["content-type"]

    def test_status_code_is_200_or_503(self, client):
        response = client.get("/health/ready")
        assert response.status_code in (200, 503)

    def test_response_contains_status_field(self, client):
        data = client.get("/health/ready").json()
        assert "status" in data
        assert data["status"] in ("ready", "degraded")

    def test_response_contains_checks_dict(self, client):
        data = client.get("/health/ready").json()
        assert "checks" in data
        assert isinstance(data["checks"], dict)

    def test_checks_include_database_key(self, client):
        data = client.get("/health/ready").json()
        assert "database" in data["checks"]

    def test_checks_include_redis_key(self, client):
        data = client.get("/health/ready").json()
        assert "redis" in data["checks"]

    def test_database_check_ok_with_test_db(self, client):
        """Database check should be 'ok' since the test engine is wired up."""
        data = client.get("/health/ready").json()
        assert data["checks"]["database"] == "ok"
