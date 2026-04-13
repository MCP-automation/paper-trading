import requests
try:
    r = requests.get('http://127.0.0.1:8000/api/debug')
    print("STATUS", r.status_code)
    print("BODY", r.text)
except Exception as e:
    print(e)
