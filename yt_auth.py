# -*- coding: utf-8 -*-
"""
유튜브 업로드용 리프레시 토큰 발급 (처음 한 번, 또는 토큰이 끊겼을 때).

    python yt_auth.py <클라이언트 ID> <클라이언트 보안 비밀번호>

브라우저에서 구글 로그인 → '지금 세계' 채널(브랜드 계정) 선택 → 허용하면
터미널에 YOUTUBE_REFRESH_TOKEN 값이 나온다. GitHub Settings → Secrets → Actions 에 넣는다.

- 클라이언트: 구글 클라우드 프로젝트 news-factory 의 OAuth 클라이언트 "news-factory-uploader" (데스크톱)
- OAuth 동의 화면이 '프로덕션' 상태여야 토큰이 7일 뒤에 끊기지 않는다 ('테스트 중'이면 7일)
- "Google에서 확인하지 않은 앱" 경고는 본인 앱이라 '고급 → 이동'으로 넘어가면 된다
"""
import http.server
import json
import sys
import urllib.parse
import urllib.request
import webbrowser

PORT = 8765
REDIRECT = "http://127.0.0.1:%d" % PORT
SCOPES = ("https://www.googleapis.com/auth/youtube.upload "
          "https://www.googleapis.com/auth/youtube.readonly")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    cid, secret = sys.argv[1], sys.argv[2]
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("완료. 이 창은 닫아도 됩니다.".encode("utf-8"))

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent"})
    print("브라우저에서 로그인하고 '지금 세계' 채널을 고르세요:\n" + url)
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        server.handle_request()
    if "error" in got:
        print("거부됨:", got["error"])
        return 1
    data = urllib.parse.urlencode({
        "code": got["code"], "client_id": cid, "client_secret": secret,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request(
            "https://oauth2.googleapis.com/token", data=data), timeout=30) as r:
        tok = json.loads(r.read())
    if "refresh_token" not in tok:
        print("리프레시 토큰이 안 왔습니다 (이미 허용된 앱이면 myaccount.google.com/permissions 에서 "
              "news-factory 접근을 지우고 다시):", {k: v for k, v in tok.items() if "token" not in k})
        return 1
    print("\nYOUTUBE_REFRESH_TOKEN =", tok["refresh_token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
