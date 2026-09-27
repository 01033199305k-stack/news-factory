// news-factory 감시기 (Cloudflare Workers, 1분 크론)
//
// USGS 지진 피드와 구글 뉴스를 1분마다 보고, 게시 기준을 넘는 새 후보가 생기면
// GitHub Actions 의 watch 워크플로를 깨운다. 판정·카드 제작·게시는 GitHub 쪽이 한다.
// 기준은 watch_usgs.py / news_watch.py 의 1차 필터와 같다 — 바꿀 땐 양쪽을 같이 바꿀 것.
//
// 바인딩: KV SEEN, 변수 GH_REPO, 시크릿 GH_DISPATCH_TOKEN (Actions: Read and write)

const USGS_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson";
// news_watch.py 의 FEEDS 와 같게 유지한다 (4개 파싱에 CPU 약 0.6ms)
const NEWS_FEEDS = [
  "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
  "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
  "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-GB&gl=GB&ceid=GB:en",
  "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-IN&gl=IN&ceid=IN:en",
];
// 매시 이 분(UTC)에 GitHub 을 깨워 '정시 정리'를 확인시킨다 (1시간 동안 게시가 없으면 1건)
const FILL_MINUTE = 5;

const MIN_MAG_WORLD = 6.0;
const MIN_MAG_NEAR = 5.0;
const NEAR_KM = 1500;
const QUAKE_MAX_AGE_H = 6;
const SEOUL = [37.5665, 126.978];

const MIN_SOURCES = 3;
const NEWS_MAX_AGE_H = 3;
const INCIDENT = new RegExp(
  "\\b(kill\\w*|dead|death\\w*|die[sd]?|dying|injur\\w*|wound\\w*|casualt\\w*|victim\\w*|" +
  "crash\\w*|collision|collid\\w*|derail\\w*|capsiz\\w*|sink\\w*|sank|shipwreck|" +
  "explosion\\w*|explod\\w*|blast\\w*|fire[s]?|blaze|wildfire\\w*|" +
  "shoot\\w*|shot|gunm[ae]n|gunfire|stabb\\w*|attack\\w*|terror\\w*|bomb\\w*|hostage\\w*|" +
  "collaps\\w*|landslide\\w*|mudslide\\w*|flood\\w*|cyclone|typhoon|hurricane|tornado\\w*|" +
  "volcan\\w*|eruption|avalanche|stampede|missing|rescue\\w*|evacuat\\w*|emergency|" +
  "arrest\\w*|manhunt|lockdown|incident)\\b", "i");
const QUAKE = /\b(earthquake|quake|tremor|magnitude)\b/i;

const SEEN_KEY = "seen";      // 이미 GitHub 을 깨운 후보 ID 목록
const LAST_KEY = "last";      // 마지막으로 깨운 기록 (확인용)
const SEEN_MAX = 800;

function kmBetween(a, b) {
  const r = (d) => (d * Math.PI) / 180;
  const [la1, lo1, la2, lo2] = [r(a[0]), r(a[1]), r(b[0]), r(b[1])];
  const h = Math.sin((la2 - la1) / 2) ** 2 +
    Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(h));
}

async function quakeCandidates() {
  const res = await fetch(USGS_FEED, { cf: { cacheTtl: 30 } });
  if (!res.ok) throw new Error("USGS " + res.status);
  const now = Date.now();
  const out = [];
  for (const f of (await res.json()).features) {
    const p = f.properties, [lon, lat] = f.geometry.coordinates;
    if (p.type !== "earthquake" || (now - p.time) / 3.6e6 > QUAKE_MAX_AGE_H) continue;
    const mag = p.mag || 0;
    const near = kmBetween(SEOUL, [lat, lon]) <= NEAR_KM;
    if (mag >= MIN_MAG_WORLD || (mag >= MIN_MAG_NEAR && near)) {
      out.push({ id: "usgs:" + f.id, title: `M${mag} ${p.place}` });
    }
  }
  return out;
}

// RSS 를 통째로 파싱하지 않고 필요한 조각만 뽑는다 (무료 플랜 CPU 10ms 안에 끝내려고)
function tag(s, name) {
  const m = s.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`));
  return m ? m[1] : "";
}

function unescapeHtml(s) {
  return s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&");
}

async function newsCandidates() {
  const now = Date.now();
  const out = new Map();
  for (const url of NEWS_FEEDS) {
    let xml;
    try {
      const res = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0 (news-factory watcher)" } });
      if (!res.ok) continue;
      xml = await res.text();
    } catch (e) {
      continue;  // 피드 하나가 막혀도 나머지로 간다
    }
    for (const item of xml.split("<item>").slice(1)) {
      const id = tag(item, "guid");
      if (!id || out.has("news:" + id)) continue;
      const pub = Date.parse(tag(item, "pubDate"));
      if (!pub || (now - pub) / 3.6e6 > NEWS_MAX_AGE_H) continue;
      const desc = unescapeHtml(tag(item, "description"));
      const sources = new Set([...desc.matchAll(/<font[^>]*>([^<]*)<\/font>/g)].map((m) => m[1].trim()));
      if (sources.size < MIN_SOURCES) continue;
      const heads = [...desc.matchAll(/<a [^>]*>([^<]*)<\/a>/g)].map((m) => m[1]).join(" ");
      if (!INCIDENT.test(heads) || QUAKE.test(heads)) continue;
      out.set("news:" + id, { id: "news:" + id, title: unescapeHtml(tag(item, "title")).slice(0, 90) });
    }
  }
  return [...out.values()];
}

async function dispatch(env, inputs = {}) {
  const body = { ref: "main" };
  if (Object.keys(inputs).length) body.inputs = inputs;
  const res = await fetch(
    `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/watch.yml/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "news-factory-watcher",
      },
      body: JSON.stringify(body),
    });
  if (res.status !== 204) throw new Error("dispatch " + res.status + " " + (await res.text()).slice(0, 200));
}

async function check(env, { dryRun = false, hourly = false } = {}) {
  // 토큰이 처음 들어왔을 때 한 번만 GitHub 을 깨워서 연결을 확인한다 (속보가 없어도 확인 가능하게)
  if (!dryRun && env.GH_DISPATCH_TOKEN && !(await env.SEEN.get("selftest"))) {
    await dispatch(env);
    await env.SEEN.put("selftest", new Date().toISOString());
    console.log("selftest dispatched");
  }
  const [quakes, news] = await Promise.all([
    quakeCandidates().catch((e) => { console.log("usgs fail", e.message); return []; }),
    newsCandidates().catch((e) => { console.log("news fail", e.message); return []; }),
  ]);
  const cands = [...quakes, ...news];
  const seen = new Set((await env.SEEN.get(SEEN_KEY, "json")) || []);
  const fresh = cands.filter((c) => !seen.has(c.id));
  if (dryRun || (fresh.length === 0 && !hourly)) return { candidates: cands, fresh, dispatched: false };

  // 정시에는 새 후보가 없어도 깨운다 — 속보 확인 + 정시 정리를 한 번에 한다
  await dispatch(env, hourly ? { fill: "true" } : {});
  if (fresh.length) {
    for (const c of fresh) seen.add(c.id);
    await env.SEEN.put(SEEN_KEY, JSON.stringify([...seen].slice(-SEEN_MAX)));
    await env.SEEN.put(LAST_KEY, JSON.stringify({ at: new Date().toISOString(), hourly, fresh }));
  }
  console.log("dispatched", hourly ? "(hourly)" : "", fresh.map((c) => c.title));
  return { candidates: cands, fresh, dispatched: true };
}

export default {
  async scheduled(event, env, ctx) {
    const hourly = new Date(event.scheduledTime).getUTCMinutes() === FILL_MINUTE;
    ctx.waitUntil(check(env, { hourly }));
  },

  // 확인용: 브라우저로 열면 지금 걸리는 후보와 마지막 기록을 보여준다 (GitHub 은 깨우지 않음)
  async fetch(request, env) {
    const r = await check(env, { dryRun: true });
    const last = await env.SEEN.get(LAST_KEY, "json");
    const selftest = await env.SEEN.get("selftest");
    return Response.json({ now: new Date().toISOString(), token: !!env.GH_DISPATCH_TOKEN,
                           selftest, ...r, last }, {
      headers: { "Cache-Control": "no-store" },
    });
  },
};
