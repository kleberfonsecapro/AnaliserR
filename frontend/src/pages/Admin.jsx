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

function nomeNivel(id) {
  return NIVEIS.find((nivel) => nivel.id === id)?.nome || id;
}

function podeGerenciar(papelAtor, alvo) {
  if (papelAtor === "admin") {
    return true;
  }
  return papelAtor === "gestor" && alvo.papel === "usuario";
}

export default function Admin({ restante, papel, usuarioId, aoSair }) {
  const [usuarios, setUsuarios] = useState([]);
  const [nivel, setNivel] = useState("usuario");
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");
  const [enviando, setEnviando] = useState(false);
  const niveisDisponiveis = papel === "admin" ? NIVEIS : NIVEIS.filter((item) => item.id === "usuario");
  const descricao = NIVEIS.find((item) => item.id === nivel)?.texto || "";

  async function carregar() {
    const lista = await listarUsuarios();
    setUsuarios(lista);
  }

  useEffect(() => {
    carregar().catch((exc) => setErro(exc.message));
  }, []);

  async function cadastrar(event) {
    event.preventDefault();
    const dados = new FormData(event.currentTarget);
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
      event.currentTarget.reset();
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

  const minutos = Math.floor(restante / 60);
  const segundos = String(restante % 60).padStart(2, "0");
  const admins = usuarios.filter((usuario) => usuario.papel === "admin").length;

  return (
    <main className="admin">
      <header>
        <div>
          <p className="olho">AnaliseR · {nomeNivel(papel)}</p>
          <h1>Usuários</h1>
          <p className="apoio">Cadastre por nível de permissão e gerencie quem já entrou.</p>
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

      <form className="cartao grade" onSubmit={cadastrar}>
        <h2>Cadastrar usuário</h2>
        <label>
          E-mail
          <input name="email" type="email" required />
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
    </main>
  );
}
