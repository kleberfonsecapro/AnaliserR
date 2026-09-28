export default function Login({ aoEntrar, erro, enviando }) {
  function enviar(event) {
    event.preventDefault();
    const dados = new FormData(event.currentTarget);
    aoEntrar(String(dados.get("email") || ""), String(dados.get("password") || ""));
  }

  return (
    <main className="login">
      <form className="cartao login-cartao" onSubmit={enviar}>
        <img className="logo-saeti" src="/logo-saeti.png" alt="SAETI" />
        <p className="orgao">Prefeitura Municipal de Cuiabá</p>
        <h1 className="marca">AnaliseR</h1>
        <p className="funcao">Sistema automático de análise de requisito</p>
        <label>
          E-mail
          <input name="email" type="email" autoComplete="username" required />
        </label>
        <label>
          Senha
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        {erro ? <p className="erro">{erro}</p> : null}
        <button type="submit" disabled={enviando}>
          {enviando ? "Entrando..." : "Entrar"}
        </button>
      </form>
    </main>
  );
}
