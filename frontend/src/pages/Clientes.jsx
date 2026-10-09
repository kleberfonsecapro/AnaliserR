import { useEffect, useState } from "react";
import { baixarPdfReuniao, detalharCliente, excluirCliente, listarClientes } from "../api";

function formatarData(iso) {
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function situacaoTexto(situacao) {
  return situacao === "parcial" ? "aberta" : "fechada";
}

export default function Clientes() {
  const [clientes, setClientes] = useState([]);
  const [ficha, setFicha] = useState(null);
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [pdfNumero, setPdfNumero] = useState(null);

  async function carregarLista(sinal) {
    const lista = await listarClientes(sinal);
    setClientes(lista);
  }

  useEffect(() => {
    const controlador = new AbortController();
    carregarLista(controlador.signal)
      .catch((exc) => {
        if (exc.name !== "AbortError") {
          setErro(exc.message);
        }
      })
      .finally(() => {
        if (!controlador.signal.aborted) {
          setCarregando(false);
        }
      });
    return () => controlador.abort();
  }, []);

  async function abrir(codigo) {
    setErro("");
    setAviso("");
    setCarregando(true);
    try {
      setFicha(await detalharCliente(codigo));
    } catch (exc) {
      setErro(exc.message);
    } finally {
      setCarregando(false);
    }
  }

  async function excluir(cliente) {
    const confirmado = window.confirm(
      `Excluir ${cliente.nome} (${cliente.codigo})? Ele some do painel e do menu do Telegram, junto com os relatórios. Isso não volta.`
    );
    if (!confirmado) {
      return;
    }
    setErro("");
    setAviso("");
    try {
      await excluirCliente(cliente.codigo);
      setFicha(null);
      setAviso(`${cliente.nome} excluído.`);
      setCarregando(true);
      await carregarLista();
    } catch (exc) {
      setErro(exc.message);
    } finally {
      setCarregando(false);
    }
  }

  async function baixar(codigo, numero) {
    setErro("");
    setPdfNumero(numero);
    try {
      await baixarPdfReuniao(codigo, numero);
    } catch (exc) {
      setErro(exc.message);
    } finally {
      setPdfNumero(null);
    }
  }

  if (ficha) {
    return (
      <>
        {erro ? <p className="erro">{erro}</p> : null}
        <article className="cartao ficha">
          <div className="cliente-topo">
            <div>
              <p className="olho">{ficha.codigo}</p>
              <h2>{ficha.nome}</h2>
              <p className="apoio">
                {ficha.total_reunioes} {ficha.total_reunioes === 1 ? "reunião" : "reuniões"} · desde{" "}
                {formatarData(ficha.data_criacao)}
              </p>
            </div>
            <div className="acoes">
              <button type="button" className="secundario" onClick={() => setFicha(null)}>
                Voltar
              </button>
              <button type="button" className="perigo" onClick={() => excluir(ficha)}>
                Excluir cliente
              </button>
            </div>
          </div>
        </article>
        {ficha.reunioes.length === 0 ? (
          <p className="apoio">Este cliente ainda não tem reunião.</p>
        ) : (
          ficha.reunioes.map((reuniao) => (
            <article className="cartao" key={reuniao.numero}>
              <div className="cliente-topo">
                <div>
                  <h2>Reunião {reuniao.numero}</h2>
                  <p className="apoio">
                    {situacaoTexto(reuniao.situacao)} · {formatarData(reuniao.data_criacao)}
                  </p>
                </div>
                <button
                  type="button"
                  className="secundario"
                  disabled={pdfNumero === reuniao.numero}
                  onClick={() => baixar(ficha.codigo, reuniao.numero)}
                >
                  {pdfNumero === reuniao.numero ? "Gerando..." : "PDF"}
                </button>
              </div>
              <pre className="relatorio">{reuniao.relatorio_gerado}</pre>
            </article>
          ))
        )}
      </>
    );
  }

  return (
    <>
      {erro ? <p className="erro">{erro}</p> : null}
      {aviso ? <p className="aviso">{aviso}</p> : null}
      {carregando ? <p className="apoio">Carregando clientes...</p> : null}
      {!carregando && clientes.length === 0 ? (
        <p className="apoio">Nenhum cliente cadastrado pelo Telegram.</p>
      ) : null}
      <section className="lista">
        {clientes.map((cliente) => (
          <button
            type="button"
            className="cartao cliente-item"
            key={cliente.codigo}
            onClick={() => abrir(cliente.codigo)}
          >
            <strong>{cliente.nome}</strong>
            <span>
              {cliente.codigo} · {cliente.total_reunioes}{" "}
              {cliente.total_reunioes === 1 ? "reunião" : "reuniões"}
            </span>
          </button>
        ))}
      </section>
    </>
  );
}
