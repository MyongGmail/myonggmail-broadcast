#!/usr/bin/env node
// crawl.mjs — 명지대 공지 9개 게시판 멱등 크롤러 v1 (의존성 0, Node 18+)
//   사용: node pipeline/crawl.mjs [--pages 3] [--board 255]
//   산출: pipeline/snapshots/<boardId>/<articleId>.html (원본) + pipeline/snapshots/index.json (파싱 결과)
//   원칙: 원본 스냅샷 보관(재분류 가능), 멱등(이미 받은 글 스킵), 요청 간 지연(서버 예의)
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';

const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname), 'snapshots');
const BASE = 'https://www.mju.ac.kr';
const BOARDS = [
  { boardId: 255, name: '일반공지' },
  { boardId: 256, name: '행사공지' },
  { boardId: 257, name: '학사공지' },
  { boardId: 259, name: '장학/학자금공지' },
  { boardId: 260, name: '진로/취업/창업공지' },
  { boardId: 261, name: '입찰공고' },
  { boardId: 4450, name: '학칙개정 사전공고' },
  { boardId: 5364, name: '학생활동공지' },
  { boardId: 8972, name: '대학안전공지' },
];

const args = process.argv.slice(2);
const flag = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : dflt;
};
const PAGES = Number(flag('pages', '2'));
const ONLY_BOARD = flag('board', null);
const DELAY_MS = 700;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const UA = 'MyongGmail-crawler/0.1 (student project; contact: canvas0420@mju.ac.kr)';

async function get(url) {
  const res = await fetch(url, { headers: { 'User-Agent': UA } });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res.text();
}

// 목록 페이지에서 글 링크 추출 — mju.ac.kr 게시판의 artclView.do 링크 패턴
function parseList(html, boardId) {
  const items = [];
  const re = /href="(\/bbs\/mjukr\/(\d+)\/(\d+)\/artclView\.do[^"]*)"[^>]*>([\s\S]*?)<\/a>/g;
  for (const m of html.matchAll(re)) {
    const [, href, , articleId, inner] = m;
    const title = inner.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
    if (title) items.push({ boardId, articleId, title, url: BASE + href.replace(/&amp;/g, '&') });
  }
  return items;
}

function extractMeta(title) {
  const campus = /\[인문/.test(title) ? '인문' : /\[자연/.test(title) ? '자연' : '공통';
  const bracket = (title.match(/^\[([^\]]+)\]/) || [])[1] || null;
  return { campus, bracket };
}

async function main() {
  await mkdir(ROOT, { recursive: true });
  const indexPath = path.join(ROOT, 'index.json');
  const index = existsSync(indexPath) ? JSON.parse(await readFile(indexPath, 'utf8')) : {};
  let fetched = 0, skipped = 0, failed = 0;

  const boards = ONLY_BOARD ? BOARDS.filter((b) => String(b.boardId) === ONLY_BOARD) : BOARDS;
  for (const b of boards) {
    await mkdir(path.join(ROOT, String(b.boardId)), { recursive: true });
    for (let page = 1; page <= PAGES; page++) {
      const listUrl = `${BASE}/mjukr/${b.boardId}/subview.do?page=${page}`;
      let listHtml;
      try {
        listHtml = await get(listUrl);
      } catch (e) {
        console.error(`[list-fail] ${b.name} p${page}: ${e.message}`);
        failed++;
        continue;
      }
      const items = parseList(listHtml, b.boardId);
      if (page === 1 && items.length === 0) console.warn(`[empty] ${b.name} — 목록 파싱 0건 (게시판 비었거나 마크업 변경)`);
      for (const it of items) {
        const key = `${it.boardId}/${it.articleId}`;
        if (index[key]) { skipped++; continue; }
        await sleep(DELAY_MS);
        try {
          const artHtml = await get(it.url);
          await writeFile(path.join(ROOT, String(it.boardId), `${it.articleId}.html`), artHtml);
          index[key] = { ...it, ...extractMeta(it.title), fetchedAt: new Date().toISOString() };
          fetched++;
          console.log(`[ok] ${b.name} ${it.articleId} ${it.title.slice(0, 40)}`);
        } catch (e) {
          console.error(`[art-fail] ${key}: ${e.message}`);
          failed++;
        }
      }
      await sleep(DELAY_MS);
    }
  }
  await writeFile(indexPath, JSON.stringify(index, null, 2));
  console.log(`\ndone: fetched=${fetched} skipped=${skipped} failed=${failed} total_indexed=${Object.keys(index).length}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
