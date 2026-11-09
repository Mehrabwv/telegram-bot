"use strict";
// Briefing: the bot's dashboard app. Plain JavaScript, no outside libraries.

const REFRESH_MS = 2 * 60 * 1000;
const data = { news: null, stocks: null, weather: null, free: null, inbox: null, info: null };
const charts = {};          // "TICKER range" -> {points, previous}
let newsFilter = "new";
let stockRange = "1d";

// ---------- small helpers ----------
const $ = (selector, root = document) => root.querySelector(selector);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value; // allowed by the page's security policy, unlike style=""
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const svgNS = "http://www.w3.org/2000/svg";
function s(tag, attrs) {
  const el = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  if (!response.ok) throw new Error((await response.text()) || response.statusText);
  return response.json();
}

function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 3200);
}

const money = (v) => (typeof v === "number" ? "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "–");
const pct = (v) => (typeof v === "number" ? (v > 0 ? "+" : "") + v.toFixed(2) + "%" : "");
const dir = (v) => (typeof v !== "number" || Math.abs(v) < 0.005 ? "flat" : v > 0 ? "up" : "down");

const ICONS = {
  clear: ["☀️", "🌙"], partly: ["⛅", "☁️"], cloudy: ["☁️", "☁️"], fog: ["🌫️", "🌫️"],
  drizzle: ["🌦️", "🌧️"], rain: ["🌧️", "🌧️"], snow: ["❄️", "❄️"], storm: ["⛈️", "⛈️"],
};
const icon = (name, day = true) => (ICONS[name] || ["🌡️", "🌡️"])[day === false ? 1 : 0];

// The bot's titles start with their own emoji; the app shows its icon separately
const bare = (title) => (title || "").replace(/^[^\p{L}\p{N}]+/u, "");
const KIND_ICONS = { alert: "🚨", morning: "🌅", stocks: "📈", night: "🌙", weather: "🌤️", display: "📌", test: "🔔" };

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date(Date.now() - 864e5);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return time;
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday " + time;
  return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + time;
}

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? "Good night" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

// Telegram-style HTML from the bot, keeping only bold, italics, links and line breaks
function safeHtml(text) {
  const doc = new DOMParser().parseFromString(`<div>${text || ""}</div>`, "text/html");
  const out = document.createDocumentFragment();
  const copy = (node, into) => {
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) { into.append(child.textContent); continue; }
      if (child.nodeType !== Node.ELEMENT_NODE) continue;
      const tag = child.tagName.toLowerCase();
      let el = null;
      if (tag === "b" || tag === "strong") el = h("b");
      else if (tag === "i" || tag === "em") el = h("i");
      else if (tag === "br") { into.append(h("br")); continue; }
      else if (tag === "a" && /^https?:\/\//i.test(child.getAttribute("href") || "")) {
        el = h("a", { href: child.getAttribute("href"), target: "_blank", rel: "noopener noreferrer" });
      }
      if (el) { copy(child, el); into.append(el); } else copy(child, into);
    }
  };
  copy(doc.body.firstChild, out);
  return out;
}

// ---------- loading ----------
async function load(name, force = false) {
  if (data[name] && !force) return data[name];
  try {
    data[name] = await api("/api/" + name);
  } catch (error) {
    if (!data[name]) data[name] = { error: error.message };
  }
  return data[name];
}

async function refreshAll() {
  await Promise.all(["news", "stocks", "inbox"].map((n) => load(n, true)));
  load("weather", true);
  updateBadge();
  render();
}

function updateBadge() {
  const link = $('.nav a[data-view="news"]');
  link.querySelector(".badge")?.remove();
  const count = (data.news?.stories || []).filter((st) => st.new && st.category !== "other").length;
  if (count) link.append(h("span", { class: "badge" }, count));
}

// ---------- stories ----------
function findStory(id) {
  return (data.news?.stories || []).find((st) => st.id === id);
}

async function rate(story, rating, buttons) {
  const value = story.rating === rating ? 0 : rating; // pressing it again takes it back
  try {
    await api("/api/rate", { id: story.id, rating: value });
    story.rating = value;
    for (const other of [findStory(story.id)].filter(Boolean)) other.rating = value;
    buttons.forEach((b) => b.classList.toggle("on", Number(b.dataset.r) === value));
    toast(value === 1 ? "👍 More stories like this" : value === -1 ? "👎 Fewer stories like this" : "Rating removed");
  } catch (error) {
    toast(error.message);
  }
}

function rateButtons(story) {
  const up = h("button", { class: "rate up", "data-r": 1, title: "More like this" }, "👍");
  const down = h("button", { class: "rate down", "data-r": -1, title: "Less like this" }, "👎");
  const both = [up, down];
  both.forEach((b) => {
    b.classList.toggle("on", Number(b.dataset.r) === story.rating);
    b.addEventListener("click", (e) => { e.stopPropagation(); rate(story, Number(b.dataset.r), both); });
  });
  return both;
}

function storyMeta(story) {
  return h("div", { class: "meta" },
    story.category !== "other" ? h("span", { class: "tag" }, story.label.replace(/^\S+\s/, "")) : null,
    h("span", null, story.section),
    story.tickers?.length ? h("span", { class: "tickers" }, story.tickers.map((t) => h("span", { class: "ticker-tag" }, t))) : null,
    story.new ? h("span", { class: "pill-new" }, "New") : null,
  );
}

function storyCard(story, compact = false) {
  return h("article", {
    class: `story cat-${story.category}${compact ? " compact" : ""}${story.new ? "" : " seen"}`,
    onclick: () => { location.hash = "story/" + story.id; },
  },
    storyMeta(story),
    h("h3", null, story.title),
    compact ? null : h("p", null, story.summary),
    compact ? null : h("div", { class: "actions" }, rateButtons(story),
      h("a", { class: "read", href: story.url, target: "_blank", rel: "noopener noreferrer",
               onclick: (e) => { e.stopPropagation(); markSeen(story); } }, "Read on NYT ↗")),
  );
}

function markSeen(story) {
  if (!story.new) return;
  story.new = false;
  api("/api/seen", { id: story.id }).catch(() => {});
  updateBadge();
}

async function openStory(id) {
  let story = findStory(id);
  if (!story) {
    try { story = await api("/api/story/" + encodeURIComponent(id)); } catch (error) { toast(error.message); return; }
  }
  markSeen(story);
  const scoreRow = (label, value) => h("div", { class: "score-row" },
    h("span", null, label), h("div", { class: "bar" }, h("span", { style: `width:${value * 10}%` })), h("b", { class: "num" }, value));
  $("#drawer-body").replaceChildren(
    h("div", { class: `cat-${story.category}` }, storyMeta(story)),
    h("h2", null, story.title),
    h("p", { class: "summary" }, story.summary || "No summary from NYT."),
    story.published ? h("p", { class: "muted small" }, "Published " + story.published.replace("T", " ")) : null,
    h("div", { class: "actions" }, rateButtons(story),
      h("a", { class: "btn primary read", href: story.url, target: "_blank", rel: "noopener noreferrer" }, "Read on NYT ↗")),
    h("div", { class: "scores" },
      h("b", null, story.rated_by_ai ? "How the AI rated it" : "Not rated by the AI yet (a guess)"),
      scoreRow("Importance", story.importance),
      scoreRow("Interest to you", story.interest),
      h("div", { class: "muted small" }, "Your 👍/👎 change how the AI rates new stories. ",
        story.category === "major" ? "Major stories this important can come as breaking news." : ""),
    ),
  );
  const drawer = $("#drawer");
  drawer.hidden = false;
  $(".drawer-close").focus();
}

function closeDrawer() {
  if ($("#drawer").hidden) return;
  $("#drawer").hidden = true;
  if (location.hash.startsWith("#story/")) history.back();
}

// ---------- charts ----------
function sparkline(points, previous) {
  const svg = s("svg", { class: "spark", viewBox: "0 0 200 56", preserveAspectRatio: "none" });
  if (!points || points.length < 2) return svg;
  const prices = points.map((p) => p[1]);
  const all = previous ? prices.concat(previous) : prices;
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const x = (i) => (i / (points.length - 1)) * 200;
  const y = (v) => 52 - ((v - lo) / span) * 48;
  const up = prices[prices.length - 1] >= (previous || prices[0]);
  const color = up ? "var(--up)" : "var(--down)";
  if (previous) svg.append(s("line", { x1: 0, x2: 200, y1: y(previous), y2: y(previous), stroke: "var(--line)", "stroke-dasharray": "3 3" }));
  svg.append(s("path", { d: prices.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(""),
                          fill: "none", stroke: color, "stroke-width": 2, "vector-effect": "non-scaling-stroke" }));
  return svg;
}

function bigChart(chart, range) {
  const wrap = h("div", { class: "chart-wrap" });
  const W = 800, H = 280, pad = { l: 8, r: 64, t: 12, b: 26 };
  const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
  wrap.append(svg);
  const points = chart?.points || [];
  if (points.length < 2) { wrap.append(h("p", { class: "empty" }, "No chart data right now.")); return wrap; }
  const prices = points.map((p) => p[1]);
  const all = chart.previous ? prices.concat(chart.previous) : prices;
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const x = (i) => pad.l + (i / (points.length - 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / span) * (H - pad.t - pad.b);
  const up = prices[prices.length - 1] >= (chart.previous || prices[0]);
  const color = up ? "var(--up)" : "var(--down)";

  for (let k = 0; k <= 3; k++) {
    const v = lo + (span * k) / 3;
    svg.append(s("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), stroke: "var(--line)", "vector-effect": "non-scaling-stroke" }));
    const label = s("text", { x: W - pad.r + 8, y: y(v) + 4, fill: "var(--muted)", "font-size": 12 });
    label.textContent = money(v);
    svg.append(label);
  }
  if (chart.previous) {
    svg.append(s("line", { x1: pad.l, x2: W - pad.r, y1: y(chart.previous), y2: y(chart.previous),
                           stroke: "var(--muted)", "stroke-dasharray": "4 4", "vector-effect": "non-scaling-stroke" }));
  }
  const line = prices.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  svg.append(s("path", { d: `${line}L${x(points.length - 1)},${H - pad.b}L${x(0)},${H - pad.b}Z`, fill: color, opacity: 0.08 }));
  svg.append(s("path", { d: line, fill: "none", stroke: color, "stroke-width": 2.2, "vector-effect": "non-scaling-stroke" }));

  const fmtTime = (t) => {
    const d = new Date(t * 1000);
    return range === "1d" ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
      : range === "5d" ? d.toLocaleString([], { weekday: "short", hour: "numeric" })
      : d.toLocaleDateString([], { month: "short", day: "numeric", year: range === "1y" ? "2-digit" : undefined });
  };
  [0, Math.floor(points.length / 2), points.length - 1].forEach((i, n) => {
    const label = s("text", { x: x(i), y: H - 6, fill: "var(--muted)", "font-size": 12,
                              "text-anchor": n === 0 ? "start" : n === 1 ? "middle" : "end" });
    label.textContent = fmtTime(points[i][0]);
    svg.append(label);
  });

  const cross = s("line", { y1: pad.t, y2: H - pad.b, stroke: "var(--muted)", "vector-effect": "non-scaling-stroke", visibility: "hidden" });
  const dot = s("circle", { r: 4, fill: color, visibility: "hidden" });
  svg.append(cross, dot);
  const tip = h("div", { class: "chart-tip", hidden: true });
  wrap.append(tip);
  const move = (event) => {
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(points.length - 1, Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (points.length - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(prices[i])); dot.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.textContent = `${money(prices[i])} · ${fmtTime(points[i][0])}`;
    const left = (x(i) / W) * box.width;
    tip.style.left = Math.min(Math.max(0, left - 60), box.width - 150) + "px";
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
  return wrap;
}

async function chartFor(ticker, range) {
  const key = `${ticker} ${range}`;
  if (!charts[key]) {
    try { charts[key] = await api(`/api/chart?ticker=${encodeURIComponent(ticker)}&range=${range}`); }
    catch { charts[key] = { points: [] }; }
  }
  return charts[key];
}

// ---------- views ----------
function pageHead(title, subtitle, ...extra) {
  return h("div", { class: "page-head" },
    h("div", null, h("h1", null, title), subtitle ? h("div", { class: "muted" }, subtitle) : null),
    h("div", { class: "spacer" }), extra);
}

function failed(what) {
  return h("div", { class: "card empty" }, `Couldn't load ${what}. Is the bot running?`);
}

function changePill(change) {
  return h("span", { class: `change num ${dir(change)}` }, typeof change === "number" ? pct(change) : "n/a");
}

async function viewToday() {
  const [news, stocks, inbox] = await Promise.all([load("news"), load("stocks"), load("inbox")]);
  const weather = data.weather;
  const date = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  const top = (news.stories || []).filter((st) => st.new).slice(0, 5);

  const weatherCard = h("a", { class: "card", href: "#weather", style: "color:inherit;text-decoration:none" },
    h("div", { class: "card-head" }, h("h2", null, "Weather"), h("span", { class: "muted small" }, weather?.place || "")),
    weather && !weather.error ? [
      h("div", { class: "hero-weather" },
        h("div", { class: "big-icon" }, icon(weather.now.icon, weather.now.day)),
        h("div", { class: "temp num" }, `${weather.now.temp}°`),
        h("div", null, h("div", null, h("b", null, weather.now.text)),
          h("div", { class: "muted small" }, `Feels ${weather.now.feels}° · H ${weather.high}° L ${weather.low}°`))),
      h("div", { class: "gear" }, (weather.items || []).map((it) =>
        h("span", { class: it.need ? "yes" : "" }, `${it.name}: ${it.need ? "yes" : "no"}`))),
    ] : h("p", { class: "muted" }, weather?.error ? "Weather unavailable right now." : "Loading…"));
  if (!weather) load("weather").then(() => { if (currentView() === "today") render(); });

  const stocksCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", null, "Stocks"), h("a", { href: "#stocks" }, `Market ${stocks.market?.toLowerCase() || ""} →`)),
    stocks.error ? h("p", { class: "muted" }, "Prices unavailable.") :
      h("div", { class: "mini-stocks" }, stocks.items.map((st) =>
        h("a", { class: "mini-stock", href: "#stock/" + st.ticker },
          h("b", null, st.ticker), h("span", { class: "muted small" }, st.name),
          h("span", { class: "num" }, money(st.price)), changePill(st.change)))));

  const inboxCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", null, "Latest from the bot"), h("a", { href: "#inbox" }, "Inbox →")),
    (inbox.items || []).length ? (inbox.items || []).slice(0, 4).map((it) =>
      h("a", { class: "list-row", href: "#inbox/" + it.id }, h("span", null, KIND_ICONS[it.kind] || "🔔"),
        h("span", null, bare(it.title)), h("span", { class: "when muted small" }, when(it.at))))
      : h("p", { class: "muted small" }, "Breaking news, digests and stock checks will show up here."));

  return [
    pageHead(`${greeting()} 👋`, date),
    h("div", { class: "grid two" }, weatherCard, stocksCard),
    h("div", { class: "card-head section-title" }, h("h2", null, "New for you"), h("a", { href: "#news" }, "All news →")),
    news.error ? failed("the news") : top.length ? h("div", { class: "stories" }, top.map((st) => storyCard(st)))
      : h("div", { class: "card empty" }, "You've seen every top story. 🎉"),
    h("div", { class: "section-title" }, inboxCard),
  ];
}

async function viewNews() {
  const news = await load("news");
  if (news.error) return [pageHead("News"), failed("the news")];
  const filters = [["new", "New to you"], ["all", "All"], ["major", "Major"], ["important", "Important"], ["techsci", "Tech & science"], ["liked", "Rated"]];
  const shown = news.stories.filter((st) =>
    newsFilter === "all" ? true : newsFilter === "new" ? st.new : newsFilter === "liked" ? st.rating !== 0 : st.category === newsFilter);
  return [
    pageHead("News", `Ranked for you by the AI${news.updated ? " · collected " + news.updated : ""} · ` +
      `you've rated ${news.ratings.liked} 👍 and ${news.ratings.disliked} 👎`),
    h("div", { class: "chips", style: "margin-bottom:16px" }, filters.map(([key, label]) =>
      h("button", { class: "chip" + (newsFilter === key ? " on" : ""), onclick: () => { newsFilter = key; render(); } },
        label, " ", h("span", { class: "muted" }, countFor(news.stories, key))))),
    shown.length ? h("div", { class: "stories" }, shown.map((st) => storyCard(st)))
      : h("div", { class: "card empty" }, newsFilter === "new" ? "You've seen every story. Check back after the next collection (every 30 minutes)." : "Nothing here."),
  ];
}

function countFor(stories, key) {
  return stories.filter((st) => key === "all" || (key === "new" ? st.new : key === "liked" ? st.rating !== 0 : st.category === key)).length;
}

async function viewStocks() {
  const stocks = await load("stocks");
  if (stocks.error) return [pageHead("Stocks"), failed("the prices")];
  const cards = stocks.items.map((st) => {
    const spark = h("div", null, sparkline([]));
    chartFor(st.ticker, "1d").then((c) => spark.replaceChildren(sparkline(c.points, c.previous)));
    return h("a", { class: "card stock-card", href: "#stock/" + st.ticker },
      h("div", { class: "stock-top" }, h("span", { class: "t" }, st.ticker), changePill(st.change)),
      h("div", { class: "muted small" }, st.name),
      h("div", { class: "price num" }, money(st.price)),
      spark,
      st.news.length ? h("div", { class: "muted small" }, `📰 ${st.news.length} recent ${st.news.length === 1 ? "story" : "stories"}`) : null);
  });
  const asOf = stocks.items.find((st) => st.time)?.time;
  return [pageHead("Stocks", `Market ${stocks.market.toLowerCase()}${asOf ? " · as of " + asOf : ""}`),
          h("div", { class: "grid cards" }, cards)];
}

async function viewStock(ticker) {
  const stocks = await load("stocks");
  const st = (stocks.items || []).find((it) => it.ticker === ticker);
  if (!st) return [h("a", { class: "back", href: "#stocks" }, "← Stocks"), h("div", { class: "card empty" }, "Not on your watchlist.")];
  const chartBox = h("div", null, h("p", { class: "loading" }, "Loading chart…"));
  const ranges = [["1d", "1D"], ["5d", "5D"], ["1mo", "1M"], ["6mo", "6M"], ["1y", "1Y"]];
  const drawChart = () => chartFor(ticker, stockRange).then((c) => chartBox.replaceChildren(bigChart(c, stockRange)));
  const chips = h("div", { class: "chips" }, ranges.map(([key, label]) =>
    h("button", { class: "chip" + (stockRange === key ? " on" : ""), onclick: (e) => {
      stockRange = key;
      chips.querySelectorAll(".chip").forEach((c) => c.classList.remove("on"));
      e.currentTarget.classList.add("on");
      drawChart();
    } }, label)));
  drawChart();
  const stat = (k, v) => h("div", { class: "stat" }, h("div", { class: "k" }, k), h("div", { class: "v num" }, v));
  return [
    h("a", { class: "back", href: "#stocks" }, "← Stocks"),
    pageHead(ticker, st.name, h("div", { style: "text-align:right" },
      h("div", { class: "price num" }, money(st.price)), changePill(st.change))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Price"), chips), chartBox,
      h("div", { class: "stats" },
        stat("Previous close", money(st.previous)),
        stat("Today's range", st.low && st.high ? `${money(st.low)} – ${money(st.high)}` : "–"),
        stat("Change", pct(st.change) || "–"),
        stat("Market", (st.open ? "Open" : "Closed") + (st.time ? ` · ${st.time}` : "")))),
    h("h2", { class: "section-title" }, "NYT stories about it"),
    st.news.length ? h("div", { class: "card" }, st.news.map((n) =>
      h("a", { class: "list-row", href: n.url, target: "_blank", rel: "noopener noreferrer" },
        h("span", null, "📰"), h("span", null, n.title), h("span", { class: "when muted small" }, n.published))))
      : h("div", { class: "card empty" }, "No NYT stories about it in the last 3 days."),
  ];
}

async function viewWeather() {
  const w = await load("weather");
  if (w.error) return [pageHead("Weather"), failed("the weather")];
  const lo = Math.min(...w.days.map((d) => d.low ?? d.high)), hi = Math.max(...w.days.map((d) => d.high));
  const spanT = hi - lo || 1;
  return [
    pageHead("Weather", `${w.place} · updated ${w.updated}`),
    h("div", { class: "grid two" },
      h("div", { class: "card" },
        h("div", { class: "hero-weather" },
          h("div", { class: "big-icon" }, icon(w.now.icon, w.now.day)),
          h("div", { class: "temp num" }, `${w.now.temp}°`),
          h("div", null, h("h2", null, w.now.text), h("div", { class: "muted" }, `Feels like ${w.now.feels}°`),
            h("div", { class: "muted small" }, `High ${w.high}° · Low ${w.low}°`))),
        h("div", { class: "stats" },
          h("div", { class: "stat" }, h("div", { class: "k" }, "Humidity"), h("div", { class: "v num" }, w.now.humidity != null ? Math.round(w.now.humidity) + "%" : "–")),
          h("div", { class: "stat" }, h("div", { class: "k" }, "Wind"), h("div", { class: "v num" }, w.now.wind != null ? Math.round(w.now.wind) + " mph" : "–")),
          h("div", { class: "stat" }, h("div", { class: "k" }, "Gusts"), h("div", { class: "v num" }, w.now.gusts != null ? Math.round(w.now.gusts) + " mph" : "–")))),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", null, `What to bring ${w.for}`), w.confidence ? h("span", { class: "conf " + w.confidence }, w.confidence + " confidence") : null),
        h("div", { class: "gear-cards" }, (w.items || []).map((it) =>
          h("div", { class: "gear-card" + (it.need ? " yes" : "") }, h("div", { class: "muted small" }, it.name),
            h("div", { class: "a" }, it.need ? "Yes" : "No"), h("div", { class: "small" }, it.note.join(" "))))),
        w.confidence_note ? h("p", { class: "muted small" }, w.confidence_note) : null)),
    h("div", { class: "card section-title" }, h("h2", { style: "margin-bottom:12px" }, "Next 24 hours"),
      h("div", { class: "hours" }, w.hours.map((hr) => h("div", { class: "hour" },
        h("div", { class: "muted" }, hr.time), h("div", { class: "i" }, icon(hr.icon, hr.day)),
        h("div", { class: "t num" }, `${hr.temp}°`), h("div", { class: "rain" }, hr.rain ? `💧${hr.rain}%` : " "),
        hr.wind != null ? h("div", { class: "muted small" }, `${hr.wind} mph`) : null)))),
    h("div", { class: "grid two section-title" },
      h("div", { class: "card" }, h("h2", { style: "margin-bottom:8px" }, "7 days"),
        h("div", { class: "days" }, w.days.map((d) => h("div", { class: "day" },
          h("div", null, h("b", null, d.name), h("div", { class: "muted small" }, d.date)),
          h("div", { style: "font-size:22px" }, icon(d.icon)),
          h("div", { class: "small" }, d.text, d.uv != null ? h("div", { class: "muted" }, `UV ${d.uv}`) : null),
          h("div", { class: "rain" }, d.rain ? `💧${d.rain}%` : ""),
          h("div", { class: "range" }, h("span", { style: `left:${((d.low ?? d.high) - lo) / spanT * 100}%;right:${(hi - d.high) / spanT * 100}%` })),
          h("div", { class: "num", style: "text-align:right" }, h("b", null, `${d.high}°`), " ", h("span", { class: "muted" }, d.low != null ? `${d.low}°` : "")))))),
      h("div", { class: "card" }, h("h2", null, "Sources"),
        h("p", { class: "muted small" }, w.weights_kind === "learned"
          ? "Each source counts by how accurate it has been at your station." : "The NWS counts double until enough forecasts are checked."),
        h("div", { class: "sources" }, Object.entries(w.sources).sort((a, b) => b[1] - a[1]).map(([name, share]) =>
          h("div", { class: "score-row" }, h("span", null, name), h("div", { class: "bar" }, h("span", { style: `width:${share}%` })), h("b", { class: "num" }, share + "%")))),
        w.accuracy ? h("p", { class: "muted small" }, w.accuracy) : null,
        w.station ? h("p", { class: "muted small" }, `Measured at ${w.station}.`) : null)),
  ];
}

// ---------- free games: cards, hover preview, details ----------
const canHover = matchMedia("(hover: hover) and (pointer: fine)").matches;
let peekTimer = null, peekCycle = null;

function gameLinks(it) {
  const info = it.info || {};
  const link = (href, text, primary) => href ? h("a", { class: "btn" + (primary ? " primary" : ""), href, target: "_blank",
    rel: "noopener noreferrer", onclick: (e) => e.stopPropagation() }, text) : null;
  return h("div", { class: "btn-row" },
    link(it.url, "Claim ↗", true),
    link(info.reviews_url, (info.reviews_label || "Reviews") + " ↗"),
    link(info.video_url, "▶ Gameplay ↗"));
}

function kindChips(info, count = 4) {
  const kinds = (info?.kind || []).slice(0, count);
  return kinds.length ? h("div", { class: "kinds" }, kinds.map((k) => h("span", null, k))) : null;
}

function ratingLine(info) {
  return info?.rating ? h("div", { class: "rating" }, info.rating.text) : null;
}

function showPeek(card, it) {
  const info = it.info;
  if (!info || (!info.pictures?.length && !info.summary)) return;
  hidePeek();
  const pictures = info.pictures?.length ? info.pictures : info.cover ? [info.cover] : [];
  const img = pictures.length ? h("img", { src: pictures[0], alt: "" }) : null;
  const dots = pictures.length > 1 ? h("div", { class: "peek-dots" }, pictures.map((_, i) => h("span", { class: i ? "" : "on" }))) : null;
  const peek = h("div", { class: "peek", id: "peek" },
    img, dots,
    h("div", { class: "peek-body" },
      h("b", null, it.title),
      kindChips(info, 5),
      info.modes?.length ? h("div", { class: "muted small" }, info.modes.join(" · ")) : null,
      ratingLine(info),
      info.summary ? h("p", null, info.summary.length > 320 ? info.summary.slice(0, 320).replace(/\s\S*$/, "") + "…" : info.summary) : null,
      h("div", { class: "muted small" }, "Click for more pictures and links")));
  document.body.append(peek);
  const box = card.getBoundingClientRect(), width = 400, gap = 12;
  const left = box.right + gap + width < innerWidth ? box.right + gap : Math.max(8, box.left - gap - width);
  peek.style.left = left + "px";
  peek.style.top = Math.max(8, Math.min(box.top, innerHeight - peek.offsetHeight - 8)) + "px";
  let shown = 0;
  if (pictures.length > 1) {
    pictures.slice(1).forEach((src) => { new Image().src = src; }); // load the rest while the first is shown
    peekCycle = setInterval(() => {
      shown = (shown + 1) % pictures.length;
      img.src = pictures[shown];
      dots.querySelectorAll("span").forEach((d, i) => d.classList.toggle("on", i === shown));
    }, 1600);
  }
}

function hidePeek() {
  clearTimeout(peekTimer);
  clearInterval(peekCycle);
  $("#peek")?.remove();
}

function openGame(it) {
  hidePeek();
  const info = it.info || {};
  const pictures = info.pictures?.length ? info.pictures : info.cover ? [info.cover] : [];
  const big = pictures.length ? h("img", { class: "gallery-main", src: pictures[0], alt: "" }) : null;
  $("#drawer-body").replaceChildren(
    h("div", { class: "where" }, it.where),
    h("h2", null, it.title),
    big,
    pictures.length > 1 ? h("div", { class: "gallery" }, pictures.map((src, i) =>
      h("img", { src, alt: "", class: i ? "" : "on", onclick: (e) => {
        big.src = src;
        e.currentTarget.parentElement.querySelectorAll("img").forEach((x) => x.classList.toggle("on", x === e.currentTarget));
      } }))) : null,
    kindChips(info, 6),
    ratingLine(info),
    info.summary ? h("p", { class: "summary" }, info.summary) : h("p", { class: "muted" }, "No description found for this one: try the reviews or gameplay links."),
    h("div", { class: "muted small" }, [info.modes?.join(" · "), info.developer && "By " + info.developer, info.released && "Released " + info.released].filter(Boolean).join("  ·  ")),
    h("p", null, it.worth ? h("span", { class: "was" }, it.worth) : null, " ", h("span", { class: "free-word" }, "Free"),
      it.ends ? h("span", { class: "muted" }, " · " + it.ends) : null),
    gameLinks(it));
  $("#drawer").hidden = false;
  $(".drawer-close").focus();
}

async function viewFree() {
  const free = await load("free");
  if (free.error) return [pageHead("Free to keep"), failed("the free games")];
  const card = (it) => {
    const info = it.info || {};
    const el = h("div", { class: "card free-card" + (it.last_day ? " last" : ""), onclick: () => openGame(it) },
      info.cover ? h("img", { class: "cover", src: info.cover, alt: "", loading: "lazy" }) : null,
      h("div", { class: "where" }, it.where, it.new ? h("span", { class: "pill-new", style: "margin-left:8px" }, "New") : null),
      h("h3", null, it.title),
      kindChips(info, 3),
      ratingLine(info),
      h("div", null, it.worth ? h("span", { class: "was" }, it.worth) : null, " ", h("span", { class: "free-word" }, "Free"),
        it.ends ? h("span", { class: "muted small" }, "  ·  " + (it.last_day ? "⏰ " : "") + it.ends) : null),
      gameLinks(it));
    if (canHover) {
      el.addEventListener("mouseenter", () => { peekTimer = setTimeout(() => showPeek(el, it), 350); });
      el.addEventListener("mouseleave", hidePeek);
    }
    return el;
  };
  const games = free.items.filter((it) => it.kind === "game"), apps = free.items.filter((it) => it.kind === "app");
  return [
    pageHead("Free to keep", "Paid games and apps you can claim for $0 and keep. Hover over one for pictures and what it is; click for more."),
    h("h2", { class: "section-title" }, `Games (${games.length})`),
    games.length ? h("div", { class: "grid cards" }, games.map(card)) : h("div", { class: "card empty" }, "No free games right now."),
    h("h2", { class: "section-title" }, `Android apps and games (${apps.length})`),
    apps.length ? h("div", { class: "grid cards" }, apps.map(card)) : h("div", { class: "card empty" }, "None posted in the last day."),
  ];
}

async function viewInbox() {
  const inbox = await load("inbox", true);
  if (inbox.error) return [pageHead("Inbox"), failed("the inbox")];
  return [
    pageHead("Inbox", "Everything the bot sent you: breaking news, digests, stock checks"),
    inbox.items.length ? h("div", { class: "card", style: "padding:4px" }, inbox.items.map((it) =>
      h("a", { class: "inbox-item", href: "#inbox/" + it.id },
        h("span", { class: "k" }, KIND_ICONS[it.kind] || "🔔"),
        h("span", null, h("b", null, bare(it.title)), h("div", { class: "b" }, it.body)),
        h("span", { class: "muted small" }, when(it.at)))))
      : h("div", { class: "card empty" }, "Nothing yet. Once the bot runs with the app, its messages land here."),
  ];
}

async function viewInboxItem(id) {
  let item;
  try { item = await api("/api/inbox/" + encodeURIComponent(id)); }
  catch (error) { return [h("a", { class: "back", href: "#inbox" }, "← Inbox"), h("div", { class: "card empty" }, error.message)]; }
  // In the app, the stories are shown as cards (with 👍/👎) instead of the message's own list
  const cards = item.stories.map((st) => findStory(st.id) || st);
  return [
    h("a", { class: "back", href: "#inbox" }, "← Inbox"),
    pageHead(`${KIND_ICONS[item.kind] || "🔔"} ${bare(item.title)}`, when(item.at)),
    item.html ? h("div", { class: "message" }, safeHtml(item.html)) : h("p", null, item.body),
    cards.length ? [h("h2", { class: "section-title" }, "Stories"), h("div", { class: "stories" }, cards.map((st) => storyCard(st)))] : null,
  ];
}

// ---------- notifications ----------
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && window.isSecureContext;

async function currentSubscription() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

function keyBytes(base64) {
  const text = atob(base64.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (base64.length % 4)) % 4));
  return Uint8Array.from(text, (c) => c.charCodeAt(0));
}

function deviceName() {
  const ua = navigator.userAgent;
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : "Browser";
  return `${browser} on ${/Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "Mac" : "a device"}`;
}

async function turnOn() {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") { toast("Notifications are blocked for this app in the browser."); return; }
  const info = await load("info", true);
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (sub) await sub.unsubscribe(); // a fresh one, in case the bot's key changed
  sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(info.push_key) });
  await api("/api/push/subscribe", { subscription: sub.toJSON(), device: deviceName() });
  toast("Notifications on for this device");
  render();
}

async function turnOff() {
  const sub = await currentSubscription();
  if (sub) {
    await api("/api/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
    await sub.unsubscribe();
  }
  toast("Notifications off for this device");
  render();
}

async function sendTest() {
  const sub = await currentSubscription();
  if (!sub) return;
  const result = await api("/api/push/test", { endpoint: sub.endpoint });
  toast(result.ok ? "Sent: it should pop up in a second" : "The push service refused it. Try turning notifications off and on.");
}

function linkSettings(info, standalone) {
  const helper = helperOn();
  let now;
  if (!standalone) now = "This is a normal browser tab, so links open right here in this browser.";
  else if (helper) now = "Links open in your default browser through the link helper.";
  else if (info.opens_here) now = "Links open in your default browser: the bot opens them on this computer.";
  else now = "Links open in Edge. Install the link helper below to open them in your default browser instead.";
  const toggle = h("input", { type: "checkbox", id: "helper", checked: helper, onchange: (e) => {
    try { localStorage.setItem(HELPER_KEY, e.target.checked ? "1" : "0"); } catch { toast("Couldn't save that in this browser."); }
    render();
  } });
  return h("div", { class: "card section-title" },
    h("h2", { style: "margin-bottom:8px" }, "Open links"),
    h("p", null, now),
    h("details", { open: standalone && !helper && !info.opens_here ? true : null },
      h("summary", null, "Link helper (for your other computers)"),
      h("ol", { class: "steps" },
        h("li", null, h("a", { href: "/setup/briefing-links.ps1", download: "briefing-links.ps1" }, "Download the link helper"),
          " and right-click it → ", h("b", null, "Run with PowerShell"), ". It only adds a link type for your Windows user (no admin)."),
        h("li", null, "Turn this on: ", h("label", { class: "check" }, toggle, " Use the link helper on this computer")),
        h("li", null, "The first link asks “Open Briefing link?”: tick ", h("b", null, "Always allow"), " and click Open.")),
      h("p", { class: "muted small" }, "On the computer the bot runs on you don't need it, until the bot moves to the Raspberry Pi.")));
}

async function viewSettings() {
  const info = await load("info", true);
  const sub = await currentSubscription().catch(() => null);
  const standalone = matchMedia("(display-mode: standalone)").matches;
  const notifications = !pushSupported()
    ? h("div", { class: "note" }, "Notifications need a secure address. On this computer open ", h("code", null, "http://localhost:8765"),
        "; on your other devices open the Tailscale address (", h("code", null, "https://…ts.net"), "). See the README, “Dashboard app”.")
    : h("div", { class: "setting" },
        h("div", null, h("div", null, h("span", { class: "status-dot" + (sub ? " on" : "") }), h("b", null, sub ? "On for this device" : "Off for this device")),
          h("div", { class: "muted small" }, `Breaking news, the morning and night digests, the stock check and weather updates. ` +
            `${info.devices || 0} device${info.devices === 1 ? "" : "s"} get them.`)),
        h("div", { class: "btn-row" }, sub
          ? [h("button", { class: "btn", onclick: sendTest }, "Send a test"), h("button", { class: "btn", onclick: turnOff }, "Turn off")]
          : h("button", { class: "btn primary", onclick: () => turnOn().catch((e) => toast(e.message)) }, "Turn on notifications")));
  return [
    pageHead("Settings"),
    info.preview ? h("div", { class: "note", style: "margin:0 0 16px" }, "Preview mode: the bot isn't running this. Ratings here go to a copy and are thrown away.") : null,
    h("div", { class: "card" }, h("h2", { style: "margin-bottom:12px" }, "Notifications"), notifications),
    linkSettings(info, standalone),
    h("div", { class: "card section-title" }, h("h2", { style: "margin-bottom:8px" }, "Install as an app"),
      standalone ? h("p", { class: "muted" }, "✓ Installed. It has its own window and Start menu entry.")
        : h("ol", { class: "steps" },
            h("li", null, "In Edge, click the ", h("b", null, "App available"), " icon at the right end of the address bar (or ⋯ → Apps → Install this site as an app)."),
            h("li", null, "Click Install. Pin it to the taskbar or Start if you like."),
            h("li", null, "Open it from there and turn on notifications on this page."))),
    h("div", { class: "card section-title" }, h("h2", { style: "margin-bottom:8px" }, "About"),
      h("p", { class: "muted small" }, "Briefing is served by your Telegram bot. Ratings you make here are the same as the 👍/👎 in Telegram, " +
        "and opening a story here means the bot won't send it to you again.")),
  ];
}

// ---------- routing ----------
function currentView() {
  return (location.hash.slice(1) || "today").split("/")[0];
}

let renderCount = 0;
async function render() {
  const [view, arg] = (location.hash.slice(1) || "today").split("/");
  if (view === "story") { openStory(decodeURIComponent(arg || "")); return; }
  $("#drawer").hidden = true;
  hidePeek();
  const navView = view === "stock" ? "stocks" : view;
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === navView));
  const ticket = ++renderCount;
  const views = { today: viewToday, news: viewNews, stocks: viewStocks, stock: () => viewStock(arg), weather: viewWeather,
                  free: viewFree, inbox: viewInbox, settings: viewSettings };
  const viewFn = view === "inbox" && arg ? () => viewInboxItem(arg) : views[view] || viewToday;
  let content;
  try { content = await viewFn(); } catch (error) { content = [h("div", { class: "card empty" }, "Something went wrong: " + error.message)]; }
  if (ticket !== renderCount) return; // a newer page was opened meanwhile
  const main = $("#main");
  const scroll = main.dataset.view === location.hash ? window.scrollY : 0;
  main.replaceChildren(...[content].flat(Infinity).filter(Boolean));
  main.dataset.view = location.hash;
  window.scrollTo(0, scroll);
  document.title = "Briefing · " + (navView.charAt(0).toUpperCase() + navView.slice(1));
}

let previousHash = "";
window.addEventListener("hashchange", () => {
  const wasStory = previousHash.startsWith("#story/");
  previousHash = location.hash;
  if (location.hash.startsWith("#story/")) { render(); return; }
  if (wasStory && $("#main").dataset.view === location.hash) { $("#drawer").hidden = true; return; }
  render();
});
document.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeDrawer(); });

// ---------- opening links in your own browser ----------
// Installed from Edge, the app would open every link in Edge. Instead:
// - with the link helper installed on this computer (Settings): through it, in the default browser
// - on the computer the bot runs on: the bot opens it in the default browser
// - otherwise (or in a normal browser tab): as a normal link
const HELPER_KEY = "briefing-link-helper";
const installedApp = () => matchMedia("(display-mode: standalone)").matches;
function helperOn() {
  try { return localStorage.getItem(HELPER_KEY) === "1"; } catch { return false; }
}
function linkMode() {
  if (!installedApp()) return "browser";
  if (helperOn()) return "helper";
  return data.info?.opens_here ? "bot" : "browser";
}
document.addEventListener("click", (e) => {
  const a = e.target.closest('a[target="_blank"]');
  if (!a || !/^https?:\/\//i.test(a.href) || e.defaultPrevented) return;
  const mode = linkMode();
  if (mode === "browser") return;
  e.preventDefault();
  if (mode === "helper") {
    location.href = "briefing-open:" + encodeURIComponent(a.href);
  } else {
    api("/api/open", { url: a.href }).then(() => toast("Opened in your browser"))
      .catch(() => window.open(a.href, "_blank", "noopener"));
  }
}, true); // capture: before the links' own click handlers, which stop the click there
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refreshAll(); });
setInterval(() => { if (document.visibilityState === "visible") refreshAll(); }, REFRESH_MS);

// A click on a notification while the app is open
navigator.serviceWorker?.addEventListener("message", (e) => {
  if (e.data?.open) { location.hash = new URL(e.data.open, location.origin).hash; refreshAll(); }
});

(async function start() {
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
  load("info");
  previousHash = location.hash;
  if (location.hash.startsWith("#story/")) {
    // Opened straight on a story (from a notification): show the news page under it
    const id = location.hash.slice(7);
    history.replaceState(null, "", "#news");
    await render();
    location.hash = "story/" + id;
  } else {
    await render();
  }
  await Promise.all([load("news"), load("weather")]);
  updateBadge();
  if (currentView() === "today") render();
})();
