import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import JSZip from "jszip";
import {
  AlertCircle,
  Archive,
  Clock,
  Download,
  Filter,
  Image as ImageIcon,
  RefreshCw,
  Search,
  Tags
} from "lucide-react";
import "./styles.css";

const API_BASE = "";

const DEFAULT_FILTERS = {
  q: "",
  exact: "",
  tag: "",
  start: "",
  end: "",
  division_id: "",
  min_view: "",
  min_reply: "",
  has_image: "",
  deleted: "",
  sort: "updated_desc",
  page: 1,
  page_size: 30
};

function App() {
  const [mode, setMode] = useState("api");
  const [stats, setStats] = useState(null);
  const [tags, setTags] = useState([]);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [results, setResults] = useState({ items: [], total: 0, page: 1, page_size: 30 });
  const [selected, setSelected] = useState(null);
  const [archive, setArchive] = useState(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    initialize();
  }, []);

  useEffect(() => {
    if (mode === "api") {
      runApiSearch(filters);
    } else if (archive) {
      runImportedSearch(filters, archive, setResults);
    }
  }, [filters, mode, archive]);

  async function initialize() {
    try {
      const loaded = await loadImportedArchive();
      if (loaded) {
        setArchive(loaded);
        setMode("import");
        setStats(buildImportedStats(loaded));
        setTags(buildImportedTags(loaded));
      }
      const apiStats = await fetchJson("/api/stats");
      const apiTags = await fetchJson("/api/tags");
      setMode("api");
      setStats(apiStats);
      setTags(apiTags.items || []);
      setMessage("");
    } catch {
      setMode((current) => (archive ? current : "import"));
      if (!archive) {
        setMessage("当前是静态浏览模式。若未启动本地 API，请导入归档导出包；GitHub Pages 默认不会公开完整数据。");
      }
    }
  }

  async function runApiSearch(nextFilters) {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      Object.entries(nextFilters).forEach(([key, value]) => {
        if (value !== "" && value !== null && value !== undefined) params.set(key, value);
      });
      const data = await fetchJson(`/api/search?${params.toString()}`);
      setResults(data);
      if (!selected && data.items?.length) {
        loadApiDetail(data.items[0].hole_id);
      }
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadApiDetail(holeId) {
    setSelected(await fetchJson(`/api/holes/${holeId}`));
  }

  function loadImportedDetail(holeId) {
    if (!archive) return;
    const rawHole = archive.holes.find((item) => item.hole_id === holeId);
    const hole = importedSummary(rawHole, archive);
    const imageByUrl = new Map((archive.images || []).map((image) => [image.url, image]));
    const imageRefs = (archive.imageRefs || []).filter((ref) => ref.hole_id === holeId);
    const images = imageRefs.map((ref) => ({ ...(imageByUrl.get(ref.url) || { url: ref.url }), floor_id: ref.floor_id }));
    const imagesByFloor = new Map();
    images.forEach((image) => {
      if (image.floor_id === null || image.floor_id === undefined) return;
      const list = imagesByFloor.get(image.floor_id) || [];
      list.push(image);
      imagesByFloor.set(image.floor_id, list);
    });
    const floors = archive.floors
      .filter((item) => item.hole_id === holeId)
      .map((floor) => ({ ...importedFloor(floor), images: imagesByFloor.get(floor.floor_id) || [] }));
    const tagsForHole = archive.tags.filter((item) => item.hole_id === holeId);
    setSelected({ hole, floors, tags: tagsForHole, images, raw: tryParseJson(rawHole?.raw_json) });
  }

  async function handleImport(file) {
    if (!file) return;
    setLoading(true);
    try {
      const parsed = await parseExportZip(file);
      await saveImportedArchive(parsed);
      setArchive(parsed);
      setMode("import");
      setStats(buildImportedStats(parsed));
      setTags(buildImportedTags(parsed));
      setFilters({ ...DEFAULT_FILTERS });
      setSelected(null);
      setMessage(`已导入 ${parsed.holes.length} 个帖子。`);
    } catch (error) {
      setMessage(`导入失败：${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  const activeTags = useMemo(() => tags.slice(0, 80), [tags]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Archive size={24} />
          <div>
            <h1>DanXi Watch</h1>
            <span>{mode === "api" ? "Local API" : "Imported Data"}</span>
          </div>
        </div>
        <StatsPanel stats={stats} />
        <ImportPanel loading={loading} onImport={handleImport} />
        {mode === "import" && !archive && <ImportHint />}
        {message && (
          <div className="notice">
            <AlertCircle size={16} />
            <span>{message}</span>
          </div>
        )}
      </aside>

      <main className="workspace">
        <SearchToolbar filters={filters} setFilters={setFilters} loading={loading} onRefresh={initialize} />
        <div className="content-grid">
          <section className="results-pane">
            <TagStrip tags={activeTags} onSelect={(tag) => setFilters({ ...filters, tag, page: 1 })} />
            <ResultList
              results={results}
              selectedId={selected?.hole?.hole_id}
              onSelect={(holeId) => (mode === "api" ? loadApiDetail(holeId) : loadImportedDetail(holeId))}
            />
          </section>
          <section className="detail-pane">
            <HoleDetail detail={selected} mode={mode} />
          </section>
        </div>
      </main>
    </div>
  );
}

function StatsPanel({ stats }) {
  const items = [
    ["帖子", stats?.holes ?? "-"],
    ["楼层", stats?.floors ?? "-"],
    ["标签", stats?.tags ?? "-"],
    ["图片", stats?.downloaded_images ?? stats?.images ?? "-"]
  ];
  return (
    <div className="stats-grid">
      {items.map(([label, value]) => (
        <div className="stat" key={label}>
          <strong>{value}</strong>
          <span>{label}</span>
        </div>
      ))}
    </div>
  );
}

function ImportPanel({ loading, onImport }) {
  return (
    <label className="import-button">
      <Download size={16} />
      <span>{loading ? "处理中" : "导入数据包"}</span>
      <input type="file" accept=".zip" onChange={(event) => onImport(event.target.files?.[0])} />
    </label>
  );
}

function ImportHint() {
  return (
    <div className="import-hint">
      <strong>导入私有归档包</strong>
      <span>本地 API 适合完整浏览；静态托管页面默认只提供前端和本地缓存，不直接公开完整备份。</span>
    </div>
  );
}

function SearchToolbar({ filters, setFilters, loading, onRefresh }) {
  const update = (key, value) => setFilters({ ...filters, [key]: value, page: 1 });
  return (
    <div className="toolbar">
      <div className="search-row">
        <label className="search-input">
          <Search size={18} />
          <input value={filters.q} onChange={(event) => update("q", event.target.value)} placeholder="模糊搜索内容、标签、楼层" />
        </label>
        <label className="search-input exact">
          <Filter size={18} />
          <input value={filters.exact} onChange={(event) => update("exact", event.target.value)} placeholder="精确短语" />
        </label>
        <button className="icon-button" onClick={onRefresh} title="刷新连接">
          <RefreshCw size={18} className={loading ? "spin" : ""} />
        </button>
      </div>
      <div className="filter-row">
        <input value={filters.tag} onChange={(event) => update("tag", event.target.value)} placeholder="Tag" />
        <input type="datetime-local" value={filters.start} onChange={(event) => update("start", event.target.value)} />
        <input type="datetime-local" value={filters.end} onChange={(event) => update("end", event.target.value)} />
        <input value={filters.division_id} onChange={(event) => update("division_id", event.target.value)} placeholder="Division" />
        <input value={filters.min_reply} onChange={(event) => update("min_reply", event.target.value)} placeholder="最少回复" />
        <input value={filters.min_view} onChange={(event) => update("min_view", event.target.value)} placeholder="最少浏览" />
        <select value={filters.has_image} onChange={(event) => update("has_image", event.target.value)}>
          <option value="">图片</option>
          <option value="true">含图片</option>
        </select>
        <select value={filters.sort} onChange={(event) => update("sort", event.target.value)}>
          <option value="updated_desc">最近更新</option>
          <option value="created_desc">最近创建</option>
          <option value="reply_desc">回复最多</option>
          <option value="view_desc">浏览最多</option>
        </select>
      </div>
    </div>
  );
}

function TagStrip({ tags, onSelect }) {
  if (!tags.length) return null;
  return (
    <div className="tag-strip">
      <Tags size={16} />
      {tags.map((tag) => (
        <button key={tag.name} onClick={() => onSelect(tag.name)}>
          {tag.name}<span>{tag.count}</span>
        </button>
      ))}
    </div>
  );
}

function ResultList({ results, selectedId, onSelect }) {
  return (
    <div className="result-list">
      <div className="result-meta">共 {results.total || 0} 条</div>
      {(results.items || []).map((item) => (
        <button
          className={`result-item ${selectedId === item.hole_id ? "active" : ""}`}
          key={item.hole_id}
          onClick={() => onSelect(item.hole_id)}
        >
          <div className="result-head">
            <strong>#{item.hole_id}</strong>
            <span>{formatTime(item.time_updated || item.time_created)}</span>
          </div>
          <p>{item.excerpt || "无内容"}</p>
          <div className="result-foot">
            <span>👀{item.view}</span>
            <span>💬{item.reply}</span>
            {item.has_image && <ImageIcon size={14} />}
            <span>{(item.tags || []).slice(0, 3).join(" / ")}</span>
          </div>
        </button>
      ))}
    </div>
  );
}

function HoleDetail({ detail, mode }) {
  if (!detail?.hole) {
    return <div className="empty-detail">选择一个帖子查看详情</div>;
  }
  const { hole, floors = [], images = [], tags = [] } = detail;
  return (
    <article className="detail">
      <header>
        <div>
          <h2>#{hole.hole_id}</h2>
          <p><Clock size={15} /> {formatTime(hole.time_created)} / {formatTime(hole.time_updated)}</p>
        </div>
        <div className="metrics">
          <span>👀 {hole.view}</span>
          <span>💬 {hole.reply}</span>
        </div>
      </header>
      <div className="detail-tags">
        {(tags.length ? tags.map((tag) => tag.name) : hole.tags || []).map((tag) => <span key={tag}>{tag}</span>)}
      </div>
      {images.length > 0 && (
        <div className="image-grid">
          {images.slice(0, 12).map((image) => (
            <a key={image.url} href={image.media_url || image.url} target="_blank" rel="noreferrer">
              <img src={mode === "api" && image.media_url ? image.media_url : image.url} alt="" loading="lazy" />
            </a>
          ))}
        </div>
      )}
      <div className="floor-list">
        {floors.map((floor) => (
          <section className="floor" key={floor.floor_id}>
            <div className="floor-head">
              <strong>#{floor.ranking ?? floor.floor_id}</strong>
              <span>{floor.anonyname || "anonymous"}</span>
              <span>{formatTime(floor.time_created)}</span>
              <span>👍 {floor.like}</span>
            </div>
            {floor.content_status === "deleted_notice" && (
              <div className="content-notice">后续已删除：{floor.content_notice || floor.latest_content || "删除提示"}</div>
            )}
            <FloorContent floor={floor} mode={mode} />
          </section>
        ))}
      </div>
    </article>
  );
}

function FloorContent({ floor, mode }) {
  const parts = splitMarkdownContent(floor.content || "");
  if (!parts.length) return <p className="floor-text muted">无内容</p>;
  return (
    <div className="floor-content">
      {parts.map((part, index) => {
        if (part.type === "text") {
          return part.text ? <span className="floor-text" key={`${index}-text`}>{part.text}</span> : null;
        }
        const image = findFloorImage(floor.images || [], part.url);
        const src = mode === "api" && image?.media_url ? image.media_url : part.url;
        return (
          <a className="inline-image" href={src} target="_blank" rel="noreferrer" key={`${index}-${part.url}`}>
            <img src={src} alt={part.alt || ""} loading="lazy" />
          </a>
        );
      })}
    </div>
  );
}

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function parseExportZip(file) {
  const zip = await JSZip.loadAsync(file);
  const readJsonl = async (name) => {
    const entry = zip.file(name);
    if (!entry) return [];
    const text = await entry.async("text");
    return text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  };
  const manifest = JSON.parse(await zip.file("manifest.json").async("text"));
  return {
    manifest,
    holes: await readJsonl("holes.jsonl"),
    floors: await readJsonl("floors.jsonl"),
    tags: await readJsonl("tags.jsonl"),
    images: await readJsonl("images.jsonl"),
    imageRefs: await readJsonl("image_refs.jsonl")
  };
}

function runImportedSearch(filters, archive, setResults) {
  const q = filters.q.trim();
  const exact = filters.exact.trim();
  const tag = filters.tag.trim();
  let items = archive.holes.map((hole) => importedSummary(hole, archive));
  if (q) items = items.filter((item) => item.searchText.includes(q));
  if (exact) items = items.filter((item) => item.searchText.includes(exact));
  if (tag) items = items.filter((item) => item.tags.some((value) => value.includes(tag)));
  if (filters.min_view) items = items.filter((item) => item.view >= Number(filters.min_view));
  if (filters.min_reply) items = items.filter((item) => item.reply >= Number(filters.min_reply));
  if (filters.has_image === "true") items = items.filter((item) => item.has_image);
  items.sort(sortImported(filters.sort));
  setResults({ items: items.slice(0, Number(filters.page_size || 30)), total: items.length, page: 1, page_size: Number(filters.page_size || 30) });
}

function importedSummary(hole, archive) {
  if (!hole) return null;
  const tags = archive.tags.filter((tag) => tag.hole_id === hole.hole_id).map((tag) => tag.name);
  const floors = archive.floors.filter((floor) => floor.hole_id === hole.hole_id);
  const searchText = [hole.search_text, ...tags].join("\n");
  return {
    hole_id: hole.hole_id,
    time_created: hole.time_created,
    time_updated: hole.time_updated,
    view: hole.view_count,
    reply: hole.reply_count,
    tags,
    has_image: archive.imageRefs.some((ref) => ref.hole_id === hole.hole_id),
    excerpt: searchText.replace(/\s+/g, " ").slice(0, 220),
    searchText,
    floors
  };
}

function importedFloor(floor) {
  return {
    floor_id: floor.floor_id,
    hole_id: floor.hole_id,
    ranking: floor.ranking,
    reply_to: floor.reply_to,
    anonyname: floor.anonyname,
    content: floor.content,
    latest_content: floor.latest_content,
    preserved_content: floor.preserved_content,
    content_status: floor.content_status || "normal",
    content_notice: floor.content_notice,
    time_created: floor.time_created,
    time_updated: floor.time_updated,
    deleted: Boolean(floor.deleted),
    like: floor.like_count,
    dislike: floor.dislike_count
  };
}

function sortImported(sort) {
  const key = sort === "created_desc" ? "time_created" : sort === "reply_desc" ? "reply" : sort === "view_desc" ? "view" : "time_updated";
  if (key === "reply" || key === "view") return (a, b) => (b[key] || 0) - (a[key] || 0);
  return (a, b) => (b[key] || "").toString().localeCompare((a[key] || "").toString());
}

function buildImportedStats(archive) {
  return { holes: archive.holes.length, floors: archive.floors.length, tags: new Set(archive.tags.map((tag) => tag.name)).size, images: archive.images.length };
}

function buildImportedTags(archive) {
  const counts = new Map();
  archive.tags.forEach((tag) => counts.set(tag.name, (counts.get(tag.name) || 0) + 1));
  return [...counts.entries()].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count);
}

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("danxi-watch", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("archives");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function saveImportedArchive(archive) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("archives", "readwrite");
    tx.objectStore("archives").put(archive, "latest");
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

async function loadImportedArchive() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("archives", "readonly");
    const request = tx.objectStore("archives").get("latest");
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error);
  });
}

function tryParseJson(value) {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function splitMarkdownContent(value) {
  const parts = [];
  const imageRe = /!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g;
  let lastIndex = 0;
  let match;
  while ((match = imageRe.exec(value)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: "text", text: value.slice(lastIndex, match.index) });
    }
    parts.push({ type: "image", alt: match[1], url: match[2] });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < value.length) {
    parts.push({ type: "text", text: value.slice(lastIndex) });
  }
  return parts;
}

function findFloorImage(images, url) {
  return images.find((image) => image.url === url) || null;
}

function formatTime(value) {
  if (!value) return "-";
  return value.replace("T", " ").replace(/\.\d+.*$/, "");
}

createRoot(document.getElementById("root")).render(<App />);
