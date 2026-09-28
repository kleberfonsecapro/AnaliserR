let token = null;
let timer = null;
let aoExpirar = () => {};

export function definirSessao(accessToken, expiresIn, quandoExpirar) {
  token = accessToken;
  aoExpirar = quandoExpirar;
  clearTimeout(timer);
  timer = setTimeout(() => {
    encerrarSessao();
  }, expiresIn * 1000);
}

export function encerrarSessao() {
  token = null;
  clearTimeout(timer);
  aoExpirar();
}

async function pedir(caminho, opcoes = {}) {
  const headers = { ...(opcoes.headers || {}) };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (opcoes.body) {
    headers["Content-Type"] = "application/json";
  }
  const resposta = await fetch(`/api${caminho}`, { ...opcoes, headers });
  if (resposta.status === 401 && token) {
    encerrarSessao();
    throw new Error("sessão expirada");
  }
  if (!resposta.ok) {
    let detalhe = "não foi possível concluir";
    try {
      const corpo = await resposta.json();
      if (typeof corpo.detail === "string") {
        detalhe = corpo.detail;
      }
    } catch {
      detalhe = resposta.statusText || detalhe;
    }
    throw new Error(detalhe);
  }
  if (resposta.status === 204) {
    return null;
  }
  return resposta.json();
}

export function entrar(email, password) {
  return pedir("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function listarUsuarios() {
  return pedir("/users");
}

export function criarUsuario(dados) {
  return pedir("/users", {
    method: "POST",
    body: JSON.stringify(dados),
  });
}

export function atualizarUsuario(id, dados) {
  return pedir(`/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(dados),
  });
}

export function excluirUsuario(id) {
  return pedir(`/users/${id}`, { method: "DELETE" });
}
