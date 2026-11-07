import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import "./index.css";

// index.html applies the saved theme before paint; mirror it onto the app root
// as soon as React starts so theme-aware components share the same state.
document.documentElement.dataset.theme =
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
