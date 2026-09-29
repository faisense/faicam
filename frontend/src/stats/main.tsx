import { render } from "preact";
import "../shared/theme.css";
import { App } from "./App";
import "./stats.css";

render(<App />, document.getElementById("app")!);
