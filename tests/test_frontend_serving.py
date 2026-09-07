def test_frontend_is_served_from_the_integrated_app(client):
    page = client.get("/")
    script = client.get("/js/app.js")

    assert page.status_code == 200
    assert "TSPO" in page.text
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")


def test_api_route_takes_precedence_over_frontend_mount(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_leads_to_the_live_room_catalog(client):
    page = client.get("/")

    assert 'id="live-room-list"' in page.text
    assert 'id="live-room-count"' in page.text
    assert "לתצוגה מקדימה של החדרים" not in page.text
    assert "רשימת חדרים אוטומטית עדיין לא הוגדרה" not in page.text
