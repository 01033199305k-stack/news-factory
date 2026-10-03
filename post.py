# -*- coding: utf-8 -*-
"""
게시 대기열(state/queue.json)을 스레드·인스타그램·유튜브·틱톡에 올린다.

    MEDIA_BASE=https://news-factory-watch.alsgur3319.workers.dev/media python post.py [--only threads|instagram|youtube|tiktok]

- 스레드: 카드 캐러셀 (JPEG)
- 인스타그램: 숏폼 영상이 있으면 릴스, 없으면 카드 캐러셀 (JPEG)
- 유튜브: 숏폼 영상이 있을 때만 쇼츠 (없거나 하루 한도를 다 쓰면 건너뜀)
- 틱톡: 숏폼 영상이 있을 때만, Buffer(무료 플랜) API 로 (틱톡 자체 API 는 심사 전이면 '나만 보기'로만 올라간다)
- 이미지·영상은 upload_media.py 가 먼저 미디어 보관소에 올려 둔다 (두 API 모두 공개 URL 로만 받는다)
- --only: 그 플랫폼만 올린다. 스레드는 카드가 나오자마자, 인스타는 영상이 나온 뒤에 올리려고 나눴다
- 토큰이 있는 플랫폼에만 올리고, 올린 결과를 item["done"] 에 적어서 다시 올리지 않는다
- 발생 후 POST_MAX_AGE_H 시간이 지난 건은 '속보'로 늦었으니 올리지 않고 expired 로 뺀다
- 한 플랫폼에서 3번 연속 실패하면 그 플랫폼은 포기하고 exit 1 → GitHub 가 실패 메일을 보낸다
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(ROOT, "state", "queue.json")
LOG = os.path.join(ROOT, "state", "posted.json")

POST_MAX_AGE_H = 6
MAX_ATTEMPTS = 3
MEDIA_BASE = os.environ.get("MEDIA_BASE", "").rstrip("/")
DEFAULT_TAGS = ["지금세계", "속보", "해외뉴스", "세계뉴스"]


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def media_url(item, name):
    return "%s/%s/%s" % (MEDIA_BASE, item["slug"], urllib.parse.quote(name))


def card_urls(item):
    return [media_url(item, n[:-4] + ".jpg") for n in item["images"]]


class Platform:
    name = ""
    host = ""
    token_env = ""

    def __init__(self):
        self.token = os.environ.get(self.token_env, "")
        self._uid = None

    def api(self, method, path, **params):
        params["access_token"] = self.token
        url = "%s/%s" % (self.host, path)
        data = None
        if method == "GET":
            url += "?" + urllib.parse.urlencode(params)
        else:
            data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as ex:
            body = ex.read().decode(errors="replace")
            try:  # 에러 메시지에 토큰이 섞여 나오지 않도록 message 만 뽑는다
                body = json.loads(body)["error"]["message"]
            except (ValueError, KeyError):
                body = body[:300]
            raise RuntimeError("%s %s → %s: %s" % (method, path, ex.code, body))


class Threads(Platform):
    name, host, token_env = "threads", "https://graph.threads.net/v1.0", "THREADS_ACCESS_TOKEN"

    def uid(self):
        if not self._uid:  # 사용자 ID 는 토큰으로 조회한다
            self._uid = os.environ.get("THREADS_USER_ID") or self.api("GET", "me", fields="id")["id"]
        return self._uid

    def wait(self, cid, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            st = self.api("GET", cid, fields="status,error_message")
            if st.get("status") == "FINISHED":
                return
            if st.get("status") in ("ERROR", "EXPIRED"):
                raise RuntimeError("container %s: %s" % (cid, st.get("error_message", st)))
            time.sleep(4)
        raise RuntimeError("container %s not ready in %ds" % (cid, timeout))

    def post(self, item):
        uid = self.uid()
        kids = [self.api("POST", "%s/threads" % uid, media_type="IMAGE", image_url=u,
                         is_carousel_item="true")["id"]
                for u in card_urls(item)[:20]]
        for k in kids:
            self.wait(k)
        text = item["text"] if len(item["text"]) <= 500 else item["text"][:499] + "…"
        params = {"media_type": "CAROUSEL", "children": ",".join(kids), "text": text}
        if item.get("topic_tag"):
            params["topic_tag"] = item["topic_tag"]
        parent = self.api("POST", "%s/threads" % uid, **params)["id"]
        self.wait(parent)
        # 방금 FINISHED 된 컨테이너도 게시가 "does not exist"·500 으로 잠깐 거절될 때가 있다 — 잠시 뒤 다시
        for n in range(3):
            try:
                mid = self.api("POST", "%s/threads_publish" % uid, creation_id=parent)["id"]
                break
            except RuntimeError:
                if n == 2:
                    raise
                time.sleep(15)
        return mid, self.api("GET", mid, fields="permalink").get("permalink", "")


class Instagram(Platform):
    """Instagram API (인스타그램 로그인). 프로페셔널 계정 필요. 이미지는 JPEG, 캐러셀 10장까지."""
    name, host, token_env = "instagram", "https://graph.instagram.com", "INSTAGRAM_ACCESS_TOKEN"

    def uid(self):
        if not self._uid:
            me = self.api("GET", "me", fields="user_id,username")
            self._uid = me.get("user_id") or me["id"]
        return self._uid

    def wait(self, cid, timeout=300):
        end = time.time() + timeout
        while time.time() < end:
            st = self.api("GET", cid, fields="status_code,status").get("status_code")
            if st in ("FINISHED", "PUBLISHED"):
                return
            if st in ("ERROR", "EXPIRED"):
                detail = self.api("GET", cid, fields="status").get("status", "")
                raise RuntimeError("container %s: %s %s" % (cid, st, detail))
            time.sleep(6)
        raise RuntimeError("container %s not ready in %ds" % (cid, timeout))

    def caption(self, item):
        tags = " ".join("#" + t.replace(" ", "").lstrip("#")
                        for t in dict.fromkeys(DEFAULT_TAGS + item.get("hashtags", [])))
        return (item["text"] + "\n\n" + tags)[:2200]

    def post(self, item):
        uid = self.uid()
        if item.get("video"):
            # 숏폼 영상 → 릴스 (피드에도 보이게)
            parent = self.api("POST", "%s/media" % uid, media_type="REELS",
                              video_url=media_url(item, item["video"]), caption=self.caption(item),
                              share_to_feed="true", thumb_offset="1500")["id"]
        else:
            kids = [self.api("POST", "%s/media" % uid, image_url=u, is_carousel_item="true")["id"]
                    for u in card_urls(item)[:10]]
            for k in kids:
                self.wait(k)
            parent = self.api("POST", "%s/media" % uid, media_type="CAROUSEL",
                              children=",".join(kids), caption=self.caption(item))["id"]
        self.wait(parent)
        mid = self.api("POST", "%s/media_publish" % uid, creation_id=parent)["id"]
        return mid, self.api("GET", mid, fields="permalink").get("permalink", "")


class Skip(Exception):
    """이 플랫폼에는 올리지 않고 넘어간다 (실패로 세지 않음)."""


class YouTube(Platform):
    """YouTube Data API v3 — 숏폼 영상만 쇼츠로 올린다. 영상이 없으면 건너뛴다.
    인증은 refresh token (YOUTUBE_CLIENT_ID·YOUTUBE_CLIENT_SECRET·YOUTUBE_REFRESH_TOKEN).
    무료 한도는 하루 약 6건 — 다 쓰면 그 건은 건너뛴다. (2026-09-29 확인: 업로드는 공개로 올라가 조회수가 쌓인다)
    한도가 적어서 조회가 나오는 소식에만 쓴다: 2026-09-27~29 쇼츠 23편 중 국제(정치·외교·전쟁)는 중앙값 499회,
    사건·사고·재난은 1~4회였다. 분류가 없는 항목(지진)과 한국 관련 소식은 그대로 올린다."""
    name, token_env = "youtube", "YOUTUBE_REFRESH_TOKEN"
    PRIVACY = os.environ.get("YOUTUBE_PRIVACY", "public")
    CATEGORIES = ("국제", "경제·과학")
    # 2026-10-03 쇼츠 20편: 한국·미국·이란·중동 소식은 760~1,170회, 에펠탑·보잉 노조·말레이시아·리투아니아·
    # 미 국채는 150~280회. 한도(하루 약 6건)를 주요국이 걸린 소식에 쓴다. 지진(분류 없음)은 그대로 올린다
    MAJOR = re.compile(r"한국|북한|미국|미군|트럼프|백악관|국방부|중국|시진핑|일본|이란|이스라엘|하마스|헤즈볼라|"
                       r"후티|사우디|러시아|푸틴|우크라이나|나토|NATO|증시|나스닥|다우|S&P|오픈AI|OpenAI|엔비디아|"
                       r"애플|테슬라|구글|머스크")
    # 시청자 언어 설정에 맞춰 보이는 제목·설명 (업로드에 같이 실려서 한도를 더 안 쓴다)
    LANGS = {"en": "English", "ja": "Japanese", "es": "Spanish",
             "zh-Hant": "Traditional Chinese (Taiwan)", "vi": "Vietnamese", "id": "Indonesian"}
    TRANSLATE_SYSTEM = """You translate a Korean breaking-news Short's title and description.
Translate faithfully. Do not add, drop, round or convert any number, name or fact.
Keep outlet names (BBC, CNN ...) as they are. Keep "[속보]"/"[정리]" as "[Breaking]"/"[Roundup]"
in the target language. Unconfirmed items must stay unconfirmed. Plain text, no hashtags."""

    def access(self):
        if not self._uid:  # _uid 자리에 액세스 토큰을 캐시한다
            data = urllib.parse.urlencode({
                "client_id": os.environ["YOUTUBE_CLIENT_ID"],
                "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
                "refresh_token": self.token, "grant_type": "refresh_token"}).encode()
            try:
                with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data,
                                            timeout=30) as r:
                    self._uid = json.loads(r.read())["access_token"]
            except urllib.error.HTTPError as ex:
                raise RuntimeError("token refresh → %d %s" % (ex.code, ex.read()[:200]))
        return self._uid

    def title(self, item):
        spec = read_json(os.path.join(ROOT, "specs", "auto", item["slug"] + ".json"), {})
        head = item["text"].split("]")[0] + "] " if item["text"].startswith("[") else ""
        topic = spec.get("topic") or item["text"][len(head):].split(".")[0]
        t = (head + topic).replace("<", "").replace(">", "")
        return t[:90] + " #Shorts"

    def video_bytes(self, item):
        local = os.path.join(ROOT, "output", item["slug"], item["video"])
        if os.path.exists(local):
            with open(local, "rb") as fp:
                return fp.read()
        req = urllib.request.Request(media_url(item, item["video"]),
                                     headers={"User-Agent": "news-factory-uploader/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()

    def localizations(self, title, text):
        """Gemini(무료)로 번역. 원문에 없는 숫자가 들어간 언어는 뺀다. 실패하면 번역 없이 올린다."""
        if not os.environ.get("GEMINI_API_KEY"):
            return {}
        import re
        from news_watch import gemini_json
        schema = {"type": "object", "properties": {"items": {"type": "array", "items": {
            "type": "object", "properties": {"lang": {"type": "string", "enum": list(self.LANGS)},
                                             "title": {"type": "string"},
                                             "description": {"type": "string"}},
            "required": ["lang", "title", "description"]}}}, "required": ["items"]}
        user = "Languages: %s\n\nTITLE:\n%s\n\nDESCRIPTION:\n%s" % (
            ", ".join("%s (%s)" % kv for kv in self.LANGS.items()), title, text)
        try:
            items = gemini_json(self.TRANSLATE_SYSTEM, user, schema)["items"]
        except Exception as ex:
            print("YOUTUBE 번역 건너뜀:", ex)
            return {}
        nums = lambda t: set(re.findall(r"\d+(?:[.,]\d+)?", t))
        allowed = nums(title + " " + text)
        out = {}
        for it in items:
            bad = nums(it["title"] + " " + it["description"]) - allowed
            if it["lang"] in self.LANGS and not bad and it["title"].strip():
                out[it["lang"]] = {"title": it["title"].replace("<", "").replace(">", "")[:90] + " #Shorts",
                                   "description": it["description"][:4900]}
            else:
                print("YOUTUBE 번역 제외", it.get("lang"), "원문에 없는 숫자:", sorted(bad))
        return out

    def add_captions(self, vid, item):
        """한국어 자막 트랙 (400 한도). 시청자는 자막 → 자동 번역으로 다른 언어를 고른다."""
        srt = os.path.join(ROOT, "output", item["slug"], "captions.srt")
        if not os.path.exists(srt):
            print("YOUTUBE 자막 파일 없음 — 건너뜀")
            return
        boundary = "nf%d" % int(time.time())
        meta = json.dumps({"snippet": {"videoId": vid, "language": "ko", "name": "한국어"}})
        with open(srt, "rb") as fp:
            body = (("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n"
                     "--%s\r\nContent-Type: application/x-subrip\r\n\r\n")
                    % (boundary, meta, boundary)).encode() + fp.read() + ("\r\n--%s--" % boundary).encode()
        req = urllib.request.Request(
            "https://www.googleapis.com/upload/youtube/v3/captions?part=snippet&uploadType=multipart",
            data=body, method="POST", headers={"Authorization": "Bearer " + self.access(),
                                               "Content-Type": "multipart/related; boundary=" + boundary})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                print("YOUTUBE 자막 올림", json.loads(r.read()).get("id"))
        except urllib.error.HTTPError as ex:  # 자막이 안 돼도 영상은 이미 올라갔다
            print("YOUTUBE 자막 실패 %d %s" % (ex.code, ex.read()[:200]))

    def post(self, item):
        if not item.get("video"):
            raise Skip("영상 없음")
        cat = item.get("category")
        if cat and cat not in self.CATEGORIES and not re.search(r"한국|북한", item.get("text", "")):
            raise Skip("유튜브는 국제·경제 소식만 (%s)" % cat)
        if cat and not self.MAJOR.search(item.get("text", "")):
            raise Skip("유튜브는 한국·주요국 관련 소식만")
        tags = list(dict.fromkeys(DEFAULT_TAGS + item.get("hashtags", [])))
        meta = {
            "snippet": {"title": self.title(item),
                        "description": (item["text"] + "\n\n" +
                                        " ".join("#" + t.replace(" ", "") for t in tags))[:4900],
                        "tags": tags, "categoryId": "25",   # 뉴스·정치
                        "defaultLanguage": "ko", "defaultAudioLanguage": "ko"},
            "status": {"privacyStatus": self.PRIVACY, "selfDeclaredMadeForKids": False},
        }
        meta["localizations"] = self.localizations(meta["snippet"]["title"][:-len(" #Shorts")],
                                                   item["text"])
        video = self.video_bytes(item)
        auth = {"Authorization": "Bearer " + self.access()}
        try:
            req = urllib.request.Request(
                "https://www.googleapis.com/upload/youtube/v3/videos"
                "?uploadType=resumable&part=snippet,status,localizations",
                data=json.dumps(meta).encode(), method="POST", headers=dict(auth, **{
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(len(video))}))
            with urllib.request.urlopen(req, timeout=60) as r:
                loc = r.headers["Location"]
            req = urllib.request.Request(loc, data=video, method="PUT",
                                         headers=dict(auth, **{"Content-Type": "video/mp4"}))
            with urllib.request.urlopen(req, timeout=600) as r:
                vid = json.loads(r.read())["id"]
        except urllib.error.HTTPError as ex:
            body = ex.read().decode(errors="replace")
            if "quotaExceeded" in body or "uploadLimitExceeded" in body:
                raise Skip("하루 업로드 한도 초과")
            try:
                body = json.loads(body)["error"]["message"]
            except (ValueError, KeyError):
                body = body[:300]
            raise RuntimeError("upload → %d: %s" % (ex.code, body))
        self.add_captions(vid, item)
        return vid, "https://youtube.com/shorts/" + vid


class TikTok(Platform):
    """틱톡 — Buffer(무료 플랜) API 로 올린다. 숏폼 영상이 있을 때만 (인스타 릴스·유튜브 쇼츠와 같은 video.mp4).
    틱톡 자체 Content Posting API 는 심사 전이면 '나만 보기'로만 올라가고, 심사도 '내 계정에 올리는 도구'는
    받아 주지 않는다. Buffer 는 틱톡 심사를 통과한 앱이라 공개로 올라간다 (2026-10-01 첫 게시로 확인).
    - 영상은 미디어 보관소 주소를 넘기면 Buffer 가 가져간다 (보관소는 3일 보관)
    - 무료 플랜 API 한도: 24시간 250회·30일 3,000회 → 건당 2~4회만 쓰게 상태 확인을 띄엄띄엄 한다
    - 하루 게시 한도(Buffer 기준 틱톡 25건, 틱톡 자체로는 보통 15건 안팎)에 걸리면 그 건은 건너뛴다
    - 해시태그는 틱톡에 5개까지만 받는다
    키: BUFFER_API_KEY (Buffer → Settings → API 의 개인 키, 2027-10-01 만료).
    채널: BUFFER_TIKTOK_CHANNEL_ID (없으면 API 로 찾는다)"""
    name, host, token_env = "tiktok", "https://api.buffer.com", "BUFFER_API_KEY"
    AI_LABEL = os.environ.get("TIKTOK_AI_LABEL", "") == "1"   # 틱톡 'AI 생성 콘텐츠' 표시 (기본 끔)
    CREATE = """mutation($input: CreatePostInput!) { createPost(input: $input) {
        __typename ... on PostActionSuccess { post { id status externalLink } }
        ... on MutationError { message } } }"""
    STATUS = """query($id: PostId!) { post(input: { id: $id }) {
        status externalLink error { message } } }"""
    LIMIT = re.compile(r"limit|too.?many|spam|한도", re.I)

    def gql(self, query, variables=None):
        req = urllib.request.Request(self.host, method="POST", data=json.dumps(
            {"query": query, "variables": variables or {}}).encode(), headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.token,
            # 파이썬 기본 User-Agent 는 Cloudflare 가 봇으로 막을 수 있다
            "User-Agent": "news-factory/1.0 (+https://github.com/01033199305k-stack/news-factory)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as ex:  # 키가 섞여 나오지 않게 응답 본문만 남긴다
            raise RuntimeError("buffer → %d: %s" % (ex.code, ex.read().decode(errors="replace")[:300]))
        if body.get("errors"):
            raise RuntimeError("buffer: " + "; ".join(e.get("message", "") for e in body["errors"])[:300])
        return body["data"]

    def channel(self):
        if not self._uid:  # _uid 자리에 Buffer 의 틱톡 채널 ID 를 캐시한다
            self._uid = os.environ.get("BUFFER_TIKTOK_CHANNEL_ID", "")
        if not self._uid:
            org = self.gql("query { account { organizations { id } } }")["account"]["organizations"][0]["id"]
            chans = self.gql("query { channels(input: { organizationId: %s }) { id service isDisconnected } }"
                             % json.dumps(org))["channels"]
            tk = [c for c in chans if c["service"] == "tiktok" and not c.get("isDisconnected")]
            if not tk:
                raise RuntimeError("Buffer 에 연결된 틱톡 채널이 없음 (끊겼으면 Buffer 에서 다시 연결)")
            self._uid = tk[0]["id"]
        return self._uid

    def caption(self, item):
        tags = [t.replace(" ", "").replace("·", "").lstrip("#")
                for t in DEFAULT_TAGS[:2] + item.get("hashtags", []) + DEFAULT_TAGS[2:]]
        tags = list(dict.fromkeys(t for t in tags if t))[:5]
        return (item["text"] + "\n\n" + " ".join("#" + t for t in tags))[:2200]

    def post(self, item):
        if not item.get("video"):
            raise Skip("영상 없음")
        res = self.gql(self.CREATE, {"input": {
            "channelId": self.channel(), "text": self.caption(item),
            "schedulingType": "automatic", "mode": "shareNow", "source": "news-factory",
            "assets": [{"video": {"url": media_url(item, item["video"]),
                                  "metadata": {"thumbnailOffset": 1500}}}],
            "metadata": {"tiktok": {"isAiGenerated": self.AI_LABEL}}}})["createPost"]
        if res.get("__typename") != "PostActionSuccess":
            msg = res.get("message", "") or str(res)
            if self.LIMIT.search(msg):
                raise Skip("하루 게시 한도: " + msg[:150])
            raise RuntimeError("createPost: " + msg[:300])
        pid = res["post"]["id"]
        # 틱톡이 영상을 받아 처리하는 데 1~2분 걸린다 (첫 게시 106초). API 한도 때문에 띄엄띄엄 본다.
        # 끝까지 못 봐도 Buffer 가 마저 올리므로 '올림'으로 적는다 — 다시 올리면 중복 게시가 된다
        link = ""
        for wait in (110, 40, 40):
            time.sleep(wait)
            st = self.gql(self.STATUS, {"id": pid})["post"]
            if st["status"] == "sent":
                link = st.get("externalLink") or ""
                break
            if st["status"] == "error":
                msg = (st.get("error") or {}).get("message", "") or "알 수 없는 오류"
                if self.LIMIT.search(msg):
                    raise Skip("틱톡 하루 한도: " + msg[:150])
                raise RuntimeError("틱톡 게시 실패 (Buffer %s): %s" % (pid, msg[:300]))
        else:
            print("TIKTOK 아직 처리 중 — Buffer 가 마저 올린다", pid)
        vid = link.rstrip("/").rsplit("/", 1)[-1] if "/video/" in link else pid
        return vid, link


def main():
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    queue = read_json(QUEUE, [])
    if not queue:
        print("대기열 비어 있음")
        return 0
    active = [p for p in (Threads(), Instagram(), YouTube(), TikTok()) if p.token]
    if not active:
        print("게시 토큰 없음 — 건너뜀 (대기 %d건)" % len(queue))
        return 0
    if not MEDIA_BASE:
        print("MEDIA_BASE 없음 — 이미지 주소를 만들 수 없어 게시 건너뜀")
        return 1
    targets = [p for p in active if only in (None, p.name)]
    print("게시 대상:", ", ".join(p.name for p in targets) or "(없음)")

    log = read_json(LOG, [])
    failed = False
    remaining = []
    for item in queue:
        # 마지막 방어선: 글자 그대로의 '\n'(역슬래시+n)이 본문에 남아 있으면 진짜 줄바꿈으로 (news_watch.fix_newlines 와 같은 일)
        item["text"] = item.get("text", "").replace("\\n", "\n")
        # 카드 제목용 강조 표시 [[ ]] 가 본문에 새어 나올 때가 있다 (2026-09-30~10-01 65건 중 3건, 모든 플랫폼에 그대로 나갔다).
        # "20% 감축 [[20% 감축]]"처럼 바로 앞 말을 되풀이한 건 하나만 남기고, 나머지는 괄호만 뗀다
        item["text"] = re.sub(r"\[\[(.+?)\]\]", r"\1",
                              re.sub(r"([^\[\]\n]{1,30}?)\s*\[\[\1\]\]", r"\1", item["text"]))
        done = item.setdefault("done", {})
        tries = item["attempts"] = (item["attempts"] if isinstance(item.get("attempts"), dict)
                                    else {})
        todo = [p for p in targets if p.name not in done]
        age_h = (time.time() * 1000 - item["event_ms"]) / 3.6e6
        if todo and age_h > POST_MAX_AGE_H:
            print("EXPIRED", item["slug"], [p.name for p in todo], "(%.1f시간 지남)" % age_h)
            log.append(dict(item, status="expired", at=int(time.time())))
            continue
        for p in todo:
            try:
                mid, link = p.post(item)
                kind = ("shorts" if p.name == "youtube" else "video" if p.name == "tiktok" else
                        "reel" if (p.name == "instagram" and item.get("video")) else "carousel")
                done[p.name] = {"id": mid, "permalink": link, "kind": kind, "at": int(time.time())}
                print("POSTED", p.name, kind, item["slug"], link)
                time.sleep(10)
            except Skip as ex:
                done[p.name] = {"skipped": str(ex), "at": int(time.time())}
                print("SKIP", p.name, item["slug"], ex)
            except Exception as ex:
                tries[p.name] = tries.get(p.name, 0) + 1
                print("FAIL", p.name, item["slug"], "attempt", tries[p.name], ex)
                if tries[p.name] >= MAX_ATTEMPTS:
                    done[p.name] = {"failed": str(ex)[:300], "at": int(time.time())}
                    failed = True
        if all(p.name in done for p in active):
            log.append(dict(item, status="done", at=int(time.time())))
        else:
            remaining.append(item)
    write_json(QUEUE, remaining)
    write_json(LOG, log)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
