import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AuthenticatedApp } from "./auth/AuthenticatedApp";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthenticatedApp />
  </StrictMode>,
);
