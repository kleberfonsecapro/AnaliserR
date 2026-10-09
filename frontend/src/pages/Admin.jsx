import { useEffect, useState } from "react";
import { atualizarUsuario, criarUsuario, excluirUsuario, listarUsuarios } from "../api";

const NIVEIS = [
  {
    id: "admin",
    nome: "Administrador",
    texto: "Entra no painel e gerencia todos os usuários.",
  },
  {
    id: "gestor",
    nome: "Gestor",
    texto: "Entra no painel e gerencia apenas usuários.",
  },
  {
    id: "usuario",
    nome: "Usuário",
    texto: "Não entra no painel. Pode usar o bot, se autorizado.",
  },
];

function podeGerenciar(papelAtor, alvo) {
  if (papelAtor === "admin") {
    return true;
  }
  return papelAtor === "gestor" && alvo.papel === "usuario";
}

export default function Admin({ papel, usuarioId }) {
  const [usuarios, setUsuarios] = useState([]);
  const [pagina, setPagina] = useState(0);
  const [temMais, setTemMais] = useState(false);
  const [nivel, setNivel] = useState("usuario");
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");
  const [enviando, setEnviando] = useState(false);
  const niveisDisponiveis = papel === "admin" ? NIVEIS : NIVEIS.filter((item) => item.id === "usuario");
  const descricao = NIVEIS.find((item) => item.id === nivel)?.texto || "";
  const POR_PAGINA = 50;

  async function carregar(paginaAlvo = pagina, sinal) {
    const lista = await listarUsuarios(paginaAlvo, POR_PAGINA, sinal);
    setUsuarios(lista);
    setTemMais(lista.length === POR_PAGINA);
  }

  useEffect(() => {
    const controlador = new AbortController();
    carregar(pagina, controlador.signal).catch((exc) => {
      if (exc.name !== "AbortError") {
        setErro(exc.message);
      }
    });
    return () => controlador.abort();
  }, [pagina]);

  async function cadastrar(event) {
    event.preventDefault();
    const formulario = event.currentTarget;
    const dados = new FormData(formulario);
    const telegram = String(dados.get("telegram_user_id") || "").trim();
    setEnviando(true);
    setErro("");
    setAviso("");
    try {
      await criarUsuario({
        email: String(dados.get("email") || ""),
        password: String(dados.get("password") || ""),
        papel: String(dados.get("papel") || "usuario"),
        telegram_user_id: telegram ? Number(telegram) : null,
        pode_usar_bot: dados.get("pode_usar_bot") === "on",
      });
      formulario.reset();
      setNivel("usuario");
      setAviso("Usuário cadastrado.");
      await carregar();
    } catch (exc) {
      setErro(exc.message);
    } finally {
      setEnviando(false);
    }
  }

  async function salvarLinha(usuario, formulario) {
    const dados = new FormData(formulario);
    const telegram = String(dados.get("telegram_user_id") || "").trim();
    setErro("");
    setAviso("");
    try {
      await atualizarUsuario(usuario.id, {
        papel: String(dados.get("papel") || usuario.papel),
        telegram_user_id: telegram ? Number(telegram) : null,
        pode_usar_bot: dados.get("pode_usar_bot") === "on",
      });
      setAviso("Usuário atualizado.");
      await carregar();
    } catch (exc) {
      setErro(exc.message);
    }
  }

  async function remover(usuario) {
    const confirmado = window.confirm(`Remover ${usuario.email}?`);
    if (!confirmado) {
      return;
    }
    setErro("");
    setAviso("");
    try {
      await excluirUsuario(usuario.id);
      setAviso("Usuário removido.");
      await carregar();
    } catch (exc) {
      setErro(exc.message);
    }
  }

  const admins = usuarios.filter((usuario) => usuario.papel === "admin").length;

  return (
    <>
      <form className="cartao grade" onSubmit={cadastrar}>
        <h2>Cadastrar usuário</h2>
        <label>
          Usuário
          <input name="email" type="text" autoComplete="off" maxLength={120} required />
        </label>
        <label>
          Senha
          <input name="password" type="password" minLength={8} required />
        </label>
        <label>
          Nível de permissão
          <select name="papel" value={nivel} onChange={(event) => setNivel(event.target.value)}>
            {niveisDisponiveis.map((item) => (
              <option key={item.id} value={item.id}>
                {item.nome}
              </option>
            ))}
          </select>
        </label>
        <label>
          ID do Telegram
          <input name="telegram_user_id" inputMode="numeric" pattern="[0-9]*" />
        </label>
        <p className="dica">{descricao}</p>
        <label className="check">
          <input name="pode_usar_bot" type="checkbox" />
          Pode usar o bot
        </label>
        <button type="submit" disabled={enviando}>
          {enviando ? "Salvando..." : "Cadastrar"}
        </button>
      </form>

      {erro ? <p className="erro">{erro}</p> : null}
      {aviso ? <p className="aviso">{aviso}</p> : null}

      <section className="lista">
        {usuarios.map((usuario) => {
          const proprio = usuario.id === usuarioId;
          const gerencia = podeGerenciar(papel, usuario);
          const ultimoAdmin = usuario.papel === "admin" && admins <= 1;
          return (
            <form
              className="cartao linha"
              key={`${usuario.id}-${usuario.papel}-${usuario.telegram_user_id ?? ""}-${usuario.pode_usar_bot}`}
              onSubmit={(event) => {
                event.preventDefault();
                salvarLinha(usuario, event.currentTarget);
              }}
            >
              <div>
                <strong>{usuario.email}</strong>
                {proprio ? <span className="papel">você</span> : null}
              </div>
              <label>
                Nível
                <select name="papel" defaultValue={usuario.papel} disabled={!gerencia || proprio}>
                  {(papel === "admin" ? NIVEIS : NIVEIS.filter((item) => item.id === usuario.papel || item.id === "usuario")).map(
                    (item) => (
                      <option key={item.id} value={item.id}>
                        {item.nome}
                      </option>
                    )
                  )}
                </select>
              </label>
              <label>
                ID do Telegram
                <input
                  name="telegram_user_id"
                  defaultValue={usuario.telegram_user_id ?? ""}
                  inputMode="numeric"
                  pattern="[0-9]*"
                  disabled={!gerencia}
                />
              </label>
              <label className="check">
                <input
                  name="pode_usar_bot"
                  type="checkbox"
                  defaultChecked={usuario.pode_usar_bot}
                  disabled={!gerencia}
                />
                Pode usar o bot
              </label>
              <div className="acoes">
                {gerencia ? (
                  <button type="submit" className="secundario">
                    Salvar
                  </button>
                ) : null}
                {gerencia && !proprio && !ultimoAdmin ? (
                  <button type="button" className="perigo" onClick={() => remover(usuario)}>
                    Remover
                  </button>
                ) : null}
              </div>
            </form>
          );
        })}
      </section>

      <nav className="paginacao" aria-label="Paginação de usuários">
        <button
          type="button"
          className="secundario"
          disabled={pagina === 0}
          onClick={() => setPagina((atual) => Math.max(0, atual - 1))}
        >
          Anterior
        </button>
        <span>Página {pagina + 1}</span>
        <button
          type="button"
          className="secundario"
          disabled={!temMais}
          onClick={() => setPagina((atual) => atual + 1)}
        >
          Próxima
        </button>
      </nav>
    </>
  );
}
