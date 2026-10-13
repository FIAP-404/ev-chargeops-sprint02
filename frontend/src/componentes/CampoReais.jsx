import { useLayoutEffect, useRef } from "react";

const FORMATO = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/**
 * Campo de valor em reais no padrão dos apps bancários: só aceita dígitos e os
 * centavos entram pela direita (1 → 0,01; 1124 → 11,24). O valor é guardado em
 * centavos inteiros, sem arredondamento de float. Acima de `maximo` (centavos),
 * o valor fica no máximo. O "R$" é um prefixo fixo, fora do texto editável.
 */
export default function CampoReais({ centavos, aoMudar, maximo = Infinity, ...props }) {
  const ref = useRef(null);

  // o cursor fica sempre no fim, onde os dígitos entram
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && document.activeElement === el) el.setSelectionRange(el.value.length, el.value.length);
  }, [centavos]);

  function aoDigitar(e) {
    const digitos = e.target.value.replace(/\D/g, "").slice(-11);
    aoMudar(Math.min(Number(digitos || 0), maximo));
  }

  return (
    <span className="campo-reais">
      <span className="prefixo" aria-hidden="true">R$</span>
      <input
        ref={ref}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        spellCheck={false}
        placeholder="0,00"
        value={centavos ? FORMATO.format(centavos / 100) : ""}
        onChange={aoDigitar}
        onFocus={(e) => e.target.setSelectionRange(e.target.value.length, e.target.value.length)}
        {...props}
      />
    </span>
  );
}
