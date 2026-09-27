# news-factory

"지금 세계" 계정(스레드 @shopping._seller)의 속보 카드뉴스 공장.
1080×1350 (4:5) PNG + 게시용 문구(caption.md)를 만든다.

## 0. 클라우드 자동 운영 (컴퓨터 꺼져 있어도 돔)

GitHub Actions `.github/workflows/watch.yml` 이 5분마다:

1. `watch_usgs.py --queue` — 새 지진이면 카드 생성 → `state/queue.json` 에 게시 대기
2. 카드 커밋·푸시 → 그 커밋의 raw 이미지 주소로
3. `post.py` — 스레드·인스타그램에 캐러셀 게시 (토큰 있는 곳만) → `state/posted.json` 에 기록

| 시크릿 (Settings → Secrets → Actions) | 내용 |
|---|---|
| `THREADS_USER_ID` | (선택) 스레드 사용자 ID. 없으면 토큰으로 조회한다 |
| `THREADS_ACCESS_TOKEN` | 스레드 장기 토큰 (60일) |
| `INSTAGRAM_ACCESS_TOKEN` | 인스타그램 API(인스타그램 로그인) 장기 토큰. 프로페셔널 계정 필요. 없으면 인스타는 건너뜀 |
| `GH_PAT` | 토큰 자동 갱신용. 이 저장소만, 권한 `Secrets: Read and write` 의 fine-grained 토큰 |
| `GEMINI_API_KEY` | 사건·사고 판정·정리용. Google AI Studio 무료 키 (카드 등록 없음) |
| `ANTHROPIC_API_KEY` | (선택, 유료) Gemini 대신 Claude 로 판정. `GEMINI_API_KEY` 가 있으면 Gemini 가 우선 |

### 사건·사고 (`news_watch.py`)

- 구글 뉴스 RSS 는 기사마다 같은 사건을 보도한 다른 매체 목록을 붙여 준다
- 자동 게시 조건: 첫 보도 3시간 안 + **서로 다른 매체 3곳 이상** + 사건·사고 키워드
- Gemini(무료) 또는 Claude 가 헤드라인만 보고 판정한다. 배경지식으로 빈칸을 채우지 않고,
  숫자엔 보도 매체를 붙이고, 매체마다 다른 숫자는 "아직 확인 안 됨"으로 보낸다
- **숫자 검증**: 카드·본문의 숫자가 헤드라인에 하나라도 없으면 게시하지 않는다 (`numbers_ok`)
- 정치·경제·스포츠·연예, 교전 당사자 한쪽 주장뿐인 전쟁 보도, 지진은 제외
- 한 번에 2건, 하루 15건까지 (스팸 판정 방지)
- `python news_watch.py --dry` 로 지금 걸리는 후보를 볼 수 있다 (Claude 호출 없음)

- 토큰이 없으면 카드만 만들고 게시는 건너뛴다 (실패 아님)
- 발생 6시간이 지난 건은 속보로 늦어서 안 올린다 (`expired`)
- 지명 사전에 없는 지역은 자동 게시하지 않고 `state/review.json` 에 두고 워크플로를 실패시킨다 → GitHub 가 메일로 알린다
- `refresh-token.yml` 이 매주 토큰을 갱신한다
- 이미지를 raw 주소로 넘기기 때문에 저장소는 **공개**여야 한다 (공개 저장소는 Actions 무료 무제한)
- GitHub 크론은 혼잡할 때 5~15분 늦게 돌 수 있다

## 1. 자동 감시 (지진)

```bash
cd "C:\Users\강민혁\news-factory"
python watch_usgs.py            # 한 번 확인
python watch_usgs.py --loop     # 3분마다 계속 확인
python watch_usgs.py --event us6000txpi   # 특정 지진으로 강제 생성
```

- 기준: 전 세계 규모 6.0 이상, 또는 서울 반경 1,500km 안 규모 5.0 이상. 발생 6시간 이내만
- 걸리면 `specs/auto/<slug>.json` → `output/<slug>/` 카드 + caption.md, 윈도우 알림
- 한 번 만든 지진은 `state/usgs_seen.json` 에 남아서 다시 안 만든다
- 수치는 전부 USGS 응답 그대로. 문장은 정해진 틀로만 만든다
- 지명 사전(`REGIONS`)에 없는 지역이면 알림에 "지명 번역 확인 필요"가 붙는다

**만드는 건 초안이다.** 게시 전에 카드와 caption.md 를 한 번 본다.

## 2. 손으로 쓰는 카드

```bash
python make_cards.py specs/sample-newcaledonia-m66.json
```

| type | 필드 | 쓰임 |
|---|---|---|
| `cover` | badge, title, sub, chips, map | 표지. badge: breaking/update/brief/explain |
| `map` | eyebrow, title, map{lat,lon,zoom,h,label} | 위치 지도 + 지구본 인셋 |
| `fact` | eyebrow, title, value, unit, sub | 숫자 하나를 크게 |
| `points` | title, items[{t,d}] | 핵심 정리 |
| `timeline` | title, events[{t,x,now}] | 시간순 |
| `check` | confirmed[], unconfirmed[] | 확인됨 / 미확인. 이 계정의 신뢰 장치 |
| `outro` | sources[], cta, cta_sub | 출처 + 팔로우 |

- 제목에서 `[[글자]]` 는 빨간 강조
- 공통: `source`(푸터 출처), `alt`(대체 텍스트), `theme`(night 기본 / day)
- 제목은 글자 수로 크기가 자동으로 줄어든다. 캔버스는 넘치면 잘리니 렌더 후 눈으로 확인

## 3. 렌더링 주의 (이 PC 한정)

- 크롬 임시 폴더는 `C:\Users\Public\news-factory-tmp` 를 쓴다. 한글 사용자 폴더 경로와
  파이썬 3.12의 `tempfile.mkdtemp`(소유자 전용 권한)에서는 헤드리스 크롬이 캡처를 못 쓴다
- chrome.exe 는 바로 반환하고 캡처는 뒤에서 끝난다 → 파일이 생길 때까지 기다린다
- 지도(d3 + Natural Earth)와 폰트(Pretendard)는 `vendor/` 에 있어서 오프라인으로 돈다

## 4. 원칙

- 보도 사진은 쓰지 않는다 (저작권). 지도·그래픽만
- 확인 안 된 건 `check` 카드의 "아직 확인 안 됨"에만 쓴다
- USGS `tsunami=1` 은 경보가 아니다. "쓰나미 정보 확인이 필요한 해역 지진"이라는 표시일 뿐
