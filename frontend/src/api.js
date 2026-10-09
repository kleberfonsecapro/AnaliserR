const CHAVE_SESSAO = "analiser.sessao";

let token = null;
let timer = null;
let aoExpirar = () => {};

function lerGuardada() {
  try {
    const bruto = sessionStorage.getItem(CHAVE_SESSAO);
    if (!bruto) {
      return null;
    }
    const dados = JSON.parse(bruto);
    if (!dados?.accessToken || !dados.expiraEm || dados.expiraEm <= Date.now()) {
      sessionStorage.removeItem(CHAVE_SESSAO);
      return null;
    }
    return dados;
  } catch {
    return null;
  }
}

function agendarFim(expiraEm) {
  clearTimeout(timer);
  const falta = Math.max(0, expiraEm - Date.now());
  timer = setTimeout(() => {
    encerrarSessao();
  }, falta);
}

export function registrarExpiracao(callback) {
  aoExpirar = callback;
}

/** Devolve a sessão ainda válida desta aba, ou null. Também religa o token. */
export function restaurarSessao() {
  const dados = lerGuardada();
  if (!dados) {
    return null;
  }
  token = dados.accessToken;
  agendarFim(dados.expiraEm);
  return {
    expiraEm: dados.expiraEm,
    restante: Math.max(0, Math.ceil((dados.expiraEm - Date.now()) / 1000)),
    minutosSessao: dados.minutosSessao,
    papel: dados.papel,
    usuarioId: dados.usuarioId,
  };
}

export function definirSessao(dados) {
  token = dados.accessToken;
  try {
    sessionStorage.setItem(CHAVE_SESSAO, JSON.stringify(dados));
  } catch {
    // A sessão segue nesta carga mesmo se o navegador recusar o armazenamento.
  }
  agendarFim(dados.expiraEm);
}

export function encerrarSessao() {
  const atual = token;
  token = null;
  clearTimeout(timer);
  try {
    sessionStorage.removeItem(CHAVE_SESSAO);
  } catch {
    // Nada a limpar se o armazenamento não estiver disponível.
  }
  if (atual) {
    // Best-effort: revoga o token antes de limpar a sessão local (SEC-004).
    fetch("/api/auth/logout", {
      method: "POST",
      headers: { Authorization: `Bearer ${atual}` },
    }).catch(() => {});
  }
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
  const { signal, ...resto } = opcoes;
  const resposta = await fetch(`/api${caminho}`, { ...resto, headers, signal });
  if (resposta.status === 401 && token) {
    encerrarSessao();
    throw new Error("sessão expirada");
  }
  if (!resposta.ok) {
    let detalhe = "não foi possível concluir";
    const texto = await resposta.text();
    if (texto) {
      try {
        const corpo = JSON.parse(texto);
        if (typeof corpo.detail === "string") {
          detalhe = corpo.detail;
        } else if (Array.isArray(corpo.detail) && corpo.detail[0]?.msg) {
          detalhe = corpo.detail[0].msg;
        }
      } catch {
        detalhe = texto;
      }
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

export function recuperarSenha(email) {
  return pedir("/auth/recuperar", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function redefinirSenha(email, codigo, password) {
  return pedir("/auth/redefinir", {
    method: "POST",
    body: JSON.stringify({ email, codigo, password }),
  });
}

export function listarUsuarios(pagina = 0, limite = 50, signal) {
  return pedir(`/users?limit=${limite}&offset=${pagina * limite}`, { signal });
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

export function listarClientes(signal) {
  return pedir("/clientes", { signal });
}

export function detalharCliente(codigo, signal) {
  return pedir(`/clientes/${encodeURIComponent(codigo)}`, { signal });
}

export function excluirCliente(codigo) {
  return pedir(`/clientes/${encodeURIComponent(codigo)}`, { method: "DELETE" });
}

export async function baixarPdfReuniao(codigo, numero) {
  const headers = {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const resposta = await fetch(
    `/api/clientes/${encodeURIComponent(codigo)}/reunioes/${numero}/pdf`,
    { headers }
  );
  if (resposta.status === 401 && token) {
    encerrarSessao();
    throw new Error("sessão expirada");
  }
  if (!resposta.ok) {
    let detalhe = "não foi possível baixar o PDF";
    const texto = await resposta.text();
    if (texto) {
      try {
        const corpo = JSON.parse(texto);
        if (typeof corpo.detail === "string") {
          detalhe = corpo.detail;
        }
      } catch {
        detalhe = texto;
      }
    }
    throw new Error(detalhe);
  }
  const blob = await resposta.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `AnaliseR-${codigo}-reuniao-${numero}.pdf`;
  link.click();
  URL.revokeObjectURL(url);
}
