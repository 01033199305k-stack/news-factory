# news-factory

"지금 세계" 계정(스레드 @shopping._seller)의 속보 카드뉴스 공장.
1080×1350 (4:5) PNG + 게시용 문구(caption.md)를 만든다.

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
