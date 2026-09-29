import { render } from "preact";
import { ToastProvider } from "../shared/components";
import "../shared/theme.css";
import { App } from "./App";
import "./cameras.css";

render(
  <ToastProvider>
    <App />
  </ToastProvider>,
  document.getElementById("app")!,
);
