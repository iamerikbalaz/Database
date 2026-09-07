import type { ReactNode } from "react";
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    dashboard: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),
    companies: (
      <>
        <path d="M3 21h18M5 21V7l7-4v18M12 9h7v12M8 9h1M8 13h1M8 17h1M15 13h1M15 17h1" />
      </>
    ),
    projects: (
      <>
        <path d="M4 7h16v13H4zM9 7V4h6v3M4 11h16M10 14h4" />
      </>
    ),
    materials: (
      <>
        <path d="m12 3 9 5-9 5-9-5 9-5Z" />
        <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
      </>
    ),
    publication: (
      <>
        <path d="M12 3v12M7 8l5-5 5 5" />
        <path d="M5 13v7h14v-7" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19 15a2 2 0 0 0 .4 2l-2.8 2.8a2 2 0 0 0-2-.4 2 2 0 0 0-1.2 1.8h-4A2 2 0 0 0 8 19.4a2 2 0 0 0-2 .4L3.2 17a2 2 0 0 0 .4-2A2 2 0 0 0 2 13.7v-4A2 2 0 0 0 3.6 8a2 2 0 0 0-.4-2L6 3.2a2 2 0 0 0 2 .4A2 2 0 0 0 9.4 2h4A2 2 0 0 0 15 3.6a2 2 0 0 0 2-.4L19.8 6a2 2 0 0 0-.4 2A2 2 0 0 0 21 9.7v4A2 2 0 0 0 19 15Z" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-4-4" />
      </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="m9 18 6-6-6-6" />,
    back: <path d="m15 18-6-6 6-6" />,
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  };
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
