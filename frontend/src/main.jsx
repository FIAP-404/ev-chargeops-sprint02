import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// fonte servida pelo próprio app (funciona offline): Montserrat, geométrica como a da GoodWe
import "@fontsource-variable/montserrat/wght.css";
import "./estilos.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
