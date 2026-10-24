"""SMS login helper: image captcha (arithmetic) -> SMS code -> AES form -> JWT.

Usage:
  export SR_MOBILE=13800000000
  python login.py captcha           # fetch the captcha image, read it yourself
  python login.py send <answer>     # trigger the SMS
  python login.py login <sms> <answer>
The resulting token is written to token.txt
"""
import base64, json, os, random, string, sys, time
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

BASE = os.environ.get("RM_BASE", "").rstrip("/")
MOBILE = os.environ.get("SR_MOBILE", "")
CAPTCHA_KEY = "captcha.key"
CAPTCHA_IMG = "captcha.png"


def fetch_captcha():
    d = requests.post(BASE + "/mobile/api/captcha/captcha", json={}, timeout=20).json()["data"]
    img = d["image"]
    b64 = img.split(",", 1)[1] if img.startswith("data:image") else img
    open(CAPTCHA_IMG, "wb").write(base64.b64decode(b64))
    open(CAPTCHA_KEY, "w").write(d["key"])
    print("captcha image saved:", CAPTCHA_IMG, "key:", d["key"])
    return d["key"]


def aes_ecb_b64(plain, key_b64):
    ct = AES.new(base64.b64decode(key_b64), AES.MODE_ECB).encrypt(pad(plain.encode(), 16))
    return base64.b64encode(ct).decode()


def send_sms(answer):
    key = fetch_captcha() if not os.path.exists(CAPTCHA_KEY) else open(CAPTCHA_KEY).read().strip()
    body = {"username": MOBILE, "password": "", "verCode": answer, "verKey": key,
            "loginType": "sms", "smsKey": "", "clientId": "", "terminal": 33}
    r = requests.post(BASE + "/mobile/api/verCode/get", json=body, timeout=25).json()
    print("verCode/get:", r.get("errno"), r.get("errmsg"))
    if r.get("errno") == 0:
        open("sms.key", "w").write(r["data"])
        print("SMS sent, smsKey =", r["data"])


def login(sms_code, answer):
    key = open(CAPTCHA_KEY).read().strip()
    d = requests.get(BASE + "/mobile/api/h5/aesKey/getAesKey", timeout=20).json()["data"]
    form = {"username": MOBILE, "password": sms_code, "verCode": answer, "verKey": key,
            "loginType": "sms", "smsKey": open("sms.key").read().strip(), "clientId": "",
            "timestamp": int(time.time() * 1000),
            "nonce": "".join(random.choices(string.ascii_lowercase + string.digits, k=13))}
    cipher = aes_ecb_b64(json.dumps(form, separators=(",", ":")), d["aesKey"])
    j = requests.post(BASE + "/mobile/api/h5/auth/login",
                      json={"data": cipher, "token": d["token"]}, timeout=25).json()
    if j.get("errno") != 0:
        print("login failed:", j)
        return
    info = requests.post(BASE + "/mobile/api/h5/auth/getAppPlusUserInfo",
                         json=j["data"], timeout=25).json()
    tok = (info.get("data") or {}).get("token")
    if tok:
        open("token.txt", "w").write(tok)
        print("token written to token.txt, user:", (info["data"].get("user") or {}).get("displayname"))


if __name__ == "__main__":
    if not MOBILE:
        raise SystemExit("set the SR_MOBILE environment variable first")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "captcha":
        fetch_captcha()
    elif cmd == "send":
        send_sms(sys.argv[2])
    elif cmd == "login":
        login(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
