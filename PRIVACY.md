# news-factory 개인정보처리방침 · Privacy Policy

최종 수정: 2026-09-28

## 한국어

news-factory 는 "지금 세계" 채널 운영자가 자기 채널(스레드·인스타그램 @jigeum.segye, 유튜브 @jigeum_segye)에
세계 속보 카드뉴스와 숏폼 영상을 올리기 위해 직접 쓰는 내부 자동화 도구입니다. 다른 사용자에게 제공하는 서비스가 아닙니다.

**수집하는 정보**
- 이용자 개인정보를 수집하지 않습니다.
- YouTube API 서비스는 운영자 본인의 YouTube 채널에 운영자가 만든 동영상을 업로드하는 데에만 씁니다
  (범위: `youtube.upload`, 연결된 채널 확인용 `youtube.readonly`).

**보관**
- OAuth 클라이언트 정보와 리프레시 토큰은 GitHub Actions 의 암호화된 시크릿에만 보관합니다.
- YouTube 에서 받는 데이터는 업로드한 동영상의 ID·주소뿐이며, 이 저장소의 게시 기록(`state/posted.json`)에만 남깁니다.
- YouTube 의 조회수·댓글·구독자 등 다른 API 데이터는 가져오거나 저장하지 않습니다.
- 어떤 데이터도 제3자에게 판매하거나 공유하지 않습니다.

**보관 기간과 삭제**
- 게시 기록의 동영상 ID·주소는 게시 이력으로 보관하며, 삭제를 요청하면 7일 안에 지웁니다.
- 접근 권한을 철회하면 리프레시 토큰은 즉시 무효가 되며, 저장된 토큰은 7일 안에 GitHub 시크릿에서 삭제합니다.
- 삭제 요청: alsgur3319@naver.com

**권한 철회**
- https://myaccount.google.com/permissions 에서 언제든 news-factory 의 접근 권한을 철회할 수 있습니다.
- 이 도구는 YouTube API 서비스를 사용합니다. [YouTube 서비스 약관](https://www.youtube.com/t/terms)과
  [Google 개인정보처리방침](https://policies.google.com/privacy)이 함께 적용됩니다.

**문의**: alsgur3319@naver.com

## English

news-factory is an internal automation tool used only by the operator of the "지금 세계 (Jigeum Segye)" channels
to publish the operator's own world-news card images and short videos to the operator's own accounts
(Threads/Instagram @jigeum.segye, YouTube @jigeum_segye). It is not offered to any other users.

**Information we collect**
- We do not collect personal information from anyone.
- YouTube API Services are used only to upload videos created by the operator to the operator's own YouTube channel
  (scopes: `youtube.upload`, and `youtube.readonly` to confirm the connected channel).

**Storage**
- The OAuth client credentials and refresh token are stored only as encrypted GitHub Actions secrets.
- The only data received from YouTube is the ID/URL of each uploaded video, kept in this repository's posting log (`state/posted.json`).
- No other YouTube API data (views, comments, subscribers, etc.) is retrieved or stored.
- No data is sold or shared with third parties.

**Retention and deletion**
- Video IDs/URLs in the posting log are kept as publishing history and are deleted within 7 days of a deletion request.
- If access is revoked, the refresh token stops working immediately, and the stored token is deleted from GitHub secrets within 7 days.
- Deletion requests: alsgur3319@naver.com

**Revoking access**
- Access can be revoked at any time at https://myaccount.google.com/permissions.
- This tool uses YouTube API Services. By using it you are also subject to the
  [YouTube Terms of Service](https://www.youtube.com/t/terms) and the [Google Privacy Policy](https://policies.google.com/privacy).

**Contact**: alsgur3319@naver.com
