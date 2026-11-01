// One header pattern for every page: title, short context (`sub`), a one-line "what is this page" (`about`),
// an optional `?` with a few tips, and tools (children) on the right.
//   <PageHeader title="Compare" sub="4 sessions" about="Line up sessions of the same workout …"
//               tips={["Hover a chart to move the map cursor", "Drag to zoom"]}>{tools}</PageHeader>
import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

export function PageHeader({ title, sub, about, tips, class: cls, children }: {
  title: ComponentChildren; sub?: ComponentChildren; about?: ComponentChildren; tips?: ComponentChildren[];
  class?: string; children?: ComponentChildren;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const hasHelp = !!(tips && tips.length);
  return (
    <header class={"pagehead" + (cls ? " " + cls : "")}>
      <div class="head">
        <h1>{title}</h1>
        {sub !== undefined && sub !== null && sub !== "" && <span class="sub">{sub}</span>}
        {hasHelp && (
          <div class="help" ref={ref}>
            <button class="help-btn" aria-expanded={open} aria-label="Tips for this page" title="Tips for this page"
                    onClick={() => setOpen(!open)}>?</button>
            {open && (
              <div class="help-pop" role="dialog">
                {about && <p class="help-about">{about}</p>}
                <ul>{tips!.map((t) => <li>{t}</li>)}</ul>
              </div>
            )}
          </div>
        )}
        {children && <div class="tools">{children}</div>}
      </div>
      {about && <p class="about">{about}</p>}
    </header>
  );
}
