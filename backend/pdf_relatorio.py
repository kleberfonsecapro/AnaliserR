"""PDF do relatório e do roadmap de uma reunião já salva."""

import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from fpdf import FPDF

_FONTE = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_FONTE_NEGRITO = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
_CUIABA = timezone(timedelta(hours=-4))
_NEGRITO = re.compile(r"\*\*(.+?)\*\*")
_CODIGO = re.compile(r"`([^`]*)`")
_ITALICO = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")


def _limpar(texto: str) -> str:
    texto = _NEGRITO.sub(r"\1", texto)
    texto = _CODIGO.sub(r"\1", texto)
    texto = _ITALICO.sub(r"\1", texto)
    return texto.replace("\u00a0", " ").strip()


def _celulas(linha: str) -> list[str]:
    return [_limpar(celula) for celula in linha.strip().strip("|").split("|")]


def _separador_de_tabela(linha: str) -> bool:
    if "|" not in linha:
        return False
    celulas = [celula.strip() for celula in linha.strip().strip("|").split("|")]
    return bool(celulas) and all(re.fullmatch(r":?-{3,}:?", celula) for celula in celulas)


def _linha_de_tabela(linha: str) -> bool:
    texto = linha.strip()
    return texto.startswith("|") and texto.count("|") >= 2 and not _separador_de_tabela(texto)


def formatar_data(quando: datetime) -> str:
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return quando.astimezone(_CUIABA).strftime("%d/%m/%Y %H:%M")


class _Relatorio(FPDF):
    def header(self) -> None:
        self.set_font("DejaVu", "B", 9)
        self.set_text_color(34, 82, 76)
        self.cell(0, 6, "AnaliseR", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(245, 199, 69)
        self.set_line_width(0.6)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("DejaVu", "", 8)
        self.set_text_color(92, 107, 102)
        self.cell(0, 8, f"Página {self.page_no()}", align="C")


def _escrever_tabela(pdf: FPDF, linhas: list[str]) -> None:
    linhas_de_dados = [linha for linha in linhas if not _separador_de_tabela(linha)]
    if not linhas_de_dados:
        return
    dados = [_celulas(linha) for linha in linhas_de_dados]
    colunas = max(len(linha) for linha in dados)
    dados = [linha + [""] * (colunas - len(linha)) for linha in dados]
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(28, 43, 40)
    with pdf.table(width=pdf.epw, first_row_as_headings=True, line_height=5) as tabela:
        for linha in dados:
            fileira = tabela.row()
            for celula in linha:
                fileira.cell(celula or " ")
    pdf.ln(2)
    pdf.set_x(pdf.l_margin)


def _escrever_bloco(pdf: FPDF, linha: str) -> None:
    pdf.set_x(pdf.l_margin)
    texto = linha.strip()
    if not texto:
        pdf.ln(2)
        return
    if texto.startswith("# "):
        pdf.ln(1)
        pdf.set_font("DejaVu", "B", 16)
        pdf.set_text_color(34, 82, 76)
        pdf.multi_cell(0, 8, _limpar(texto[2:]))
        pdf.ln(1)
        return
    if texto.startswith("## "):
        pdf.ln(2)
        pdf.set_font("DejaVu", "B", 13)
        pdf.set_text_color(54, 121, 98)
        pdf.multi_cell(0, 7, _limpar(texto[3:]))
        pdf.ln(1)
        return
    if texto.startswith("### "):
        pdf.set_font("DejaVu", "B", 11)
        pdf.set_text_color(34, 82, 76)
        pdf.multi_cell(0, 6, _limpar(texto[4:]))
        return
    item = re.match(r"^(\d+)\.\s+(.*)$", texto)
    if item:
        pdf.set_font("DejaVu", "", 11)
        pdf.set_text_color(28, 43, 40)
        pdf.multi_cell(0, 6, f"{item.group(1)}. {_limpar(item.group(2))}")
        return
    if texto.startswith(("- ", "* ")):
        pdf.set_font("DejaVu", "", 11)
        pdf.set_text_color(28, 43, 40)
        pdf.multi_cell(0, 6, f"• {_limpar(texto[2:])}")
        return
    pdf.set_font("DejaVu", "", 11)
    pdf.set_text_color(28, 43, 40)
    pdf.multi_cell(0, 6, _limpar(texto))


def gerar_pdf(relatorio: str, numero: int, quando: datetime, situacao: str = "fechada") -> bytes:
    if not _FONTE.is_file() or not _FONTE_NEGRITO.is_file():
        raise RuntimeError("fonte do PDF não encontrada")
    pdf = _Relatorio()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_font("DejaVu", "", str(_FONTE))
    pdf.add_font("DejaVu", "B", str(_FONTE_NEGRITO))
    pdf.add_page()
    pdf.set_font("DejaVu", "", 11)
    pdf.set_text_color(92, 107, 102)
    if situacao == "parcial":
        cabecalho = (
            f"Reunião {numero}  ·  {formatar_data(quando)}"
            "  ·  Relatório parcial — análise aberta"
        )
    else:
        cabecalho = f"Reunião {numero}  ·  {formatar_data(quando)}"
    pdf.multi_cell(0, 6, cabecalho)
    pdf.ln(1)

    pendente: list[str] = []

    def despejar() -> None:
        if pendente:
            _escrever_tabela(pdf, pendente)
            pendente.clear()

    for linha in relatorio.replace("\r\n", "\n").split("\n"):
        if _linha_de_tabela(linha) or (_separador_de_tabela(linha) and pendente):
            pendente.append(linha)
            continue
        despejar()
        _escrever_bloco(pdf, linha)
    despejar()
    return bytes(pdf.output())
