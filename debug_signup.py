from app import create_app

app = create_app()
client = app.test_client()
resp = client.post(
    '/auth',
    data={
        'mode': 'signup',
        'username': 'TestUser123',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    },
    follow_redirects=False
)
print('STATUS', resp.status_code)
print('LOCATION', resp.headers.get('Location'))
print(resp.get_data(as_text=True)[:500])
