import { useState } from "react";
import { recuperarSenha, redefinirSenha } from "../api";

function IconeOlho({ aberto }) {
  return aberto ? (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
      <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  );
}

function CampoSenha({ rotulo, nome = "password", autoComplete = "current-password",
                      minLength }) {
  const [visivel, setVisivel] = useState(false);
  return (
    <label>
      {rotulo}
      <span className="campo-senha">
        <input
          name={nome}
          type={visivel ? "text" : "password"}
          autoComplete={autoComplete}
          minLength={minLength}
          required
        />
        <button
          type="button"
          className="olho-senha"
          onClick={() => setVisivel((v) => !v)}
          aria-label={visivel ? "Esconder a senha" : "Mostrar a senha"}
          aria-pressed={visivel}
          title={visivel ? "Esconder a senha" : "Mostrar a senha"}
        >
          <IconeOlho aberto={visivel} />
        </button>
      </span>
    </label>
  );
}

export default function Login({ aoEntrar, erro, enviando }) {
  // etapas: entrar → pedir código → redefinir com o código recebido no Telegram
  const [etapa, setEtapa] = useState("entrar");
  const [emailRecuperacao, setEmailRecuperacao] = useState("");
  const [aviso, setAviso] = useState("");
  const [erroRecuperacao, setErroRecuperacao] = useState("");
  const [ocupado, setOcupado] = useState(false);

  function enviar(event) {
    event.preventDefault();
    const dados = new FormData(event.currentTarget);
    aoEntrar(String(dados.get("email") || ""), String(dados.get("password") || ""));
  }

  async function pedirCodigo(event) {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get("email") || "");
    setOcupado(true);
    setErroRecuperacao("");
    try {
      const resposta = await recuperarSenha(email);
      setEmailRecuperacao(email);
      setAviso(resposta.mensagem);
      setEtapa("redefinir");
    } catch (exc) {
      setErroRecuperacao(exc.message);
    } finally {
      setOcupado(false);
    }
  }

  async function trocarSenha(event) {
    event.preventDefault();
    const dados = new FormData(event.currentTarget);
    setOcupado(true);
    setErroRecuperacao("");
    try {
      const resposta = await redefinirSenha(
        emailRecuperacao,
        String(dados.get("codigo") || ""),
        String(dados.get("password") || "")
      );
      setAviso(resposta.mensagem);
      setEtapa("entrar");
    } catch (exc) {
      setErroRecuperacao(exc.message);
    } finally {
      setOcupado(false);
    }
  }

  if (etapa === "pedir") {
    return (
      <main className="login">
        <form className="cartao login-cartao" onSubmit={pedirCodigo}>
          <img className="logo-saeti" src="/logo-saeti.png" alt="SAETI" />
          <h1 className="marca">Recuperar senha</h1>
          <p className="funcao">Um código de uso único chega no seu Telegram.</p>
          <label>
            Usuário
            <input name="email" type="text" autoComplete="username" maxLength={120} required />
          </label>
          {erroRecuperacao ? <p className="erro">{erroRecuperacao}</p> : null}
          <button type="submit" disabled={ocupado}>
            {ocupado ? "Enviando..." : "Enviar código"}
          </button>
          <button type="button" className="secundario" onClick={() => setEtapa("entrar")}>
            Voltar ao login
          </button>
        </form>
      </main>
    );
  }

  if (etapa === "redefinir") {
    return (
      <main className="login">
        <form className="cartao login-cartao" onSubmit={trocarSenha}>
          <img className="logo-saeti" src="/logo-saeti.png" alt="SAETI" />
          <h1 className="marca">Senha nova</h1>
          <p className="funcao">{aviso}</p>
          <label>
            Código recebido no Telegram
            <input name="codigo" type="text" minLength={8} maxLength={8}
                   autoComplete="one-time-code" required />
          </label>
          <CampoSenha rotulo="Senha nova (mínimo 8 caracteres)"
                      autoComplete="new-password" minLength={8} />
          {erroRecuperacao ? <p className="erro">{erroRecuperacao}</p> : null}
          <button type="submit" disabled={ocupado}>
            {ocupado ? "Gravando..." : "Trocar a senha"}
          </button>
          <button type="button" className="secundario" onClick={() => setEtapa("pedir")}>
            Pedir outro código
          </button>
        </form>
      </main>
    );
  }

  return (
    <main className="login">
      <form className="cartao login-cartao" onSubmit={enviar}>
        <img className="logo-saeti" src="/logo-saeti.png" alt="SAETI" />
        <h1 className="marca">AnaliseR</h1>
        <p className="funcao">Sistema automático de análise de requisito</p>
        {aviso ? <p className="aviso">{aviso}</p> : null}
        <label>
          Usuário
          <input name="email" type="text" autoComplete="username" maxLength={120} required />
        </label>
        <CampoSenha rotulo="Senha" />
        {erro ? <p className="erro">{erro}</p> : null}
        <button type="submit" disabled={enviando}>
          {enviando ? "Entrando..." : "Entrar"}
        </button>
        <button type="button" className="secundario"
                onClick={() => { setAviso(""); setEtapa("pedir"); }}>
          Esqueci a senha
        </button>
      </form>
    </main>
  );
}
