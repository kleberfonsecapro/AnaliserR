import { useEffect, useRef, useState } from "react";
import { definirSessao, encerrarSessao, entrar, registrarExpiracao, restaurarSessao } from "./api";
import Admin from "./pages/Admin";
import Clientes from "./pages/Clientes";
import Login from "./pages/Login";

const NOME_PAPEL = {
  admin: "Administrador",
  gestor: "Gestor",
  usuario: "Usuário",
};

function Painel({ restante, papel, usuarioId, aoSair }) {
  const [aba, setAba] = useState(() => {
    try {
      return sessionStorage.getItem("analiser.aba") === "clientes" ? "clientes" : "usuarios";
    } catch {
      return "usuarios";
    }
  });
  function escolherAba(proxima) {
    setAba(proxima);
    try {
      sessionStorage.setItem("analiser.aba", proxima);
    } catch {
      // A aba volta para Usuários no próximo carregamento.
    }
  }
  const minutos = Math.floor(restante / 60);
  const segundos = String(restante % 60).padStart(2, "0");
  const titulo = aba === "clientes" ? "Clientes" : "Usuários";
  const apoio =
    aba === "clientes"
      ? "Veja os clientes do Telegram e os relatórios de cada reunião."
      : "Cadastre por nível de permissão e gerencie quem já entrou.";

  return (
    <main className="admin">
      <header>
        <div>
          <p className="olho">AnaliseR · {NOME_PAPEL[papel] || papel}</p>
          <h1>{titulo}</h1>
          <p className="apoio">{apoio}</p>
        </div>
        <div className="sessao">
          <span>
            Sessão {minutos}:{segundos}
          </span>
          <button type="button" className="secundario" onClick={aoSair}>
            Sair
          </button>
        </div>
      </header>
      <nav className="abas" aria-label="Seções do painel">
        <button
          type="button"
          className={aba === "usuarios" ? "aba ativa" : "aba"}
          onClick={() => escolherAba("usuarios")}
        >
          Usuários
        </button>
        <button
          type="button"
          className={aba === "clientes" ? "aba ativa" : "aba"}
          onClick={() => escolherAba("clientes")}
        >
          Clientes
        </button>
      </nav>
      {aba === "clientes" ? <Clientes /> : <Admin papel={papel} usuarioId={usuarioId} />}
    </main>
  );
}

export default function App() {
  const expirarRef = useRef(() => {});
  const [sessao, setSessao] = useState(() => {
    registrarExpiracao(() => expirarRef.current());
    return restaurarSessao();
  });
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
  expirarRef.current = expirar;

  async function aoEntrar(email, password) {
    setEnviando(true);
    setErro("");
    try {
      const resposta = await entrar(email, password);
      const dados = {
        accessToken: resposta.access_token,
        expiraEm: Date.now() + resposta.expires_in * 1000,
        minutosSessao: Math.max(1, Math.round(resposta.expires_in / 60)),
        papel: resposta.papel,
        usuarioId: resposta.usuario_id,
      };
      definirSessao(dados);
      setSessao({
        expiraEm: dados.expiraEm,
        restante: resposta.expires_in,
        minutosSessao: dados.minutosSessao,
        papel: dados.papel,
        usuarioId: dados.usuarioId,
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
    <Painel
      restante={sessao.restante}
      papel={sessao.papel}
      usuarioId={sessao.usuarioId}
      aoSair={sair}
    />
  );
}
