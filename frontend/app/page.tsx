"use client";

import { FormEvent, useState } from "react";

type Match = { page: number; row: string[]; image: string; image_full: string };
type Result = {
  pdf_id: string;
  name: string;
  year: string;
  n_pages: number;
  available: boolean;
  file: string;
  first_page_image: string;
  first_page_image_full: string;
  matches: Match[];
};
type SearchResponse = { query: string; normalized: string; total: number; results: Result[] };

export default function Home() {
  const [usn, setUsn] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<SearchResponse | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const q = usn.trim();
    if (q.length < 6) {
      setError("Enter a full USN, e.g. 1RV23AI104");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/search?usn=${encodeURIComponent(q)}`);
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail ?? `Request failed (${res.status})`);
      }
      setData(await res.json());
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="wrap">
      <h1>NSS activity points lookup</h1>
      <p className="muted">Find every list that mentions your USN.</p>

      <form onSubmit={onSubmit} className="search">
        <label htmlFor="usn">Enter your USN</label>
        <div className="row">
          <input
            id="usn"
            value={usn}
            onChange={(e) => setUsn(e.target.value)}
            placeholder="1RV23AI104"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            autoFocus
          />
          <button type="submit" disabled={loading}>
            {loading ? "Searching…" : "Search"}
          </button>
        </div>
      </form>

      <div aria-live="polite">
        {error && <p className="error">{error}</p>}

        {data && data.total === 0 && (
          <p className="muted">
            No list mentions <strong>{data.query}</strong>. Check the USN, or the OCR may have
            misread it on the page.
          </p>
        )}

        {data && data.total > 0 && (
          <>
            <p className="muted">
              Found <strong>{data.query}</strong> in {data.total} PDF{data.total > 1 ? "s" : ""}.
            </p>
            <div className="downloads">
              <a
                className="btn"
                href={`/api/download?usn=${encodeURIComponent(data.query)}&format=images`}
                download
              >
                Download ZIP (images)
              </a>
              <a
                className="btn secondary"
                href={`/api/download?usn=${encodeURIComponent(data.query)}&format=pdf`}
                download
              >
                Download ZIP (PDF pages)
              </a>
            </div>
            <p className="muted small">
              One folder per PDF, named after the PDF, with its first page and the page that
              contains your USN.
            </p>
          </>
        )}
      </div>

      {data?.results.map((r) => (
        <section className="card" key={r.pdf_id}>
          <header className="card-head">
            <h2>{r.name}</h2>
            <span className="badge">{r.year}</span>
          </header>

          {!r.available ? (
            <p className="muted">The PDF file for this list was not found on the server.</p>
          ) : (
            <div className="pair">
              <figure>
                <a href={r.first_page_image_full} target="_blank" rel="noreferrer">
                  <img src={r.first_page_image} alt={`First page of ${r.name}`} loading="lazy" />
                </a>
                <figcaption>First page</figcaption>
              </figure>

              <span className="sep" aria-hidden="true">
                :::
              </span>

              {r.matches.map((m) => (
                <figure key={m.page}>
                  <a href={m.image_full} target="_blank" rel="noreferrer">
                    <img
                      src={m.image}
                      alt={`Page ${m.page} of ${r.name}, with the USN highlighted`}
                      loading="lazy"
                    />
                  </a>
                  <figcaption>
                    Page {m.page} of {r.n_pages} ·{" "}
                    <a href={`${r.file}#page=${m.page}`} target="_blank" rel="noreferrer">
                      open PDF here
                    </a>
                    {m.row.length > 0 && <div className="row-text">{m.row.join(" · ")}</div>}
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
        </section>
      ))}
    </main>
  );
}
