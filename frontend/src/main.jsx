import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// fonte servida pelo próprio app (funciona offline): Archivo com eixos de largura e peso
import "@fontsource-variable/archivo/wdth.css";
import "./estilos.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
