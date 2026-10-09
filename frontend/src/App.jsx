import { useEffect, useState } from "react";
import { definirSessao, encerrarSessao, entrar } from "./api";
import Admin from "./pages/Admin";
import Login from "./pages/Login";

export default function App() {
  const [sessao, setSessao] = useState(null);
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    if (!sessao) {
      return undefined;
    }
    const relogio = setInterval(() => {
      setSessao((atual) => {
        if (!atual) {
          return atual;
        }
        const restante = Math.max(0, Math.ceil((atual.expiraEm - Date.now()) / 1000));
        return { ...atual, restante };
      });
    }, 1000);
    return () => clearInterval(relogio);
  }, [sessao?.expiraEm]);

  function expirar() {
    setSessao(null);
    const minutos = sessao?.minutosSessao ?? 5;
    setErro(`A sessão de ${minutos} ${minutos === 1 ? "minuto" : "minutos"} acabou. Entre de novo.`);
  }

  async function aoEntrar(email, password) {
    setEnviando(true);
    setErro("");
    try {
      const resposta = await entrar(email, password);
      definirSessao(resposta.access_token, resposta.expires_in, expirar);
      setSessao({
        expiraEm: Date.now() + resposta.expires_in * 1000,
        restante: resposta.expires_in,
        minutosSessao: Math.max(1, Math.round(resposta.expires_in / 60)),
        papel: resposta.papel,
        usuarioId: resposta.usuario_id,
      });
    } catch (exc) {
      setErro(exc.message);
    } finally {
      setEnviando(false);
    }
  }

  function sair() {
    encerrarSessao();
    setSessao(null);
    setErro("");
  }

  if (!sessao) {
    return <Login aoEntrar={aoEntrar} erro={erro} enviando={enviando} />;
  }

  return (
    <Admin
      restante={sessao.restante}
      papel={sessao.papel}
      usuarioId={sessao.usuarioId}
      aoSair={sair}
    />
  );
}
