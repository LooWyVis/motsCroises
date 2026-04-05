#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Crossword Studio
================

Application Python pour générer une grille de mots croisés en LaTeX à partir
soit d'un fichier JSON, soit d'une saisie manuelle.

Fonctionnalités :
- interface graphique professionnelle (Tkinter + ttk)
- import JSON via explorateur de fichiers
- saisie / modification directe des mots et définitions
- export dans un dossier projet dédié contenant :
    - mots.json
    - crossword.tex
    - crossword.pdf (optionnel si pdflatex est installé)
- mode ligne de commande conservé pour automatisation

Formats JSON acceptés :
1) ["CHAT", "ARBRE", "PYTHON"]
2) [{"mot": "chat", "definition": "Animal domestique"}, ...]
3) {"mots": ["CHAT", "ARBRE"]}
4) {"mots": [{"mot": "chat", "indice": "Animal domestique"}, ...]}

Utilisation CLI :
    python crossword_to_latex.py mots.json crossword.tex --attempts 300 --seed 42

Utilisation GUI :
    python crossword_to_latex.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except Exception:  # pragma: no cover - utile pour mode CLI headless
    tk = None
    filedialog = None
    messagebox = None
    ttk = None
    ScrolledText = None


H = "H"
V = "V"
DEFAULT_TITLE = "Mots croisés"
DEFAULT_ATTEMPTS = 300
DEFAULT_CELL_SIZE_CM = 0.72


@dataclass(frozen=True)
class Entry:
    word: str
    clue: Optional[str] = None
    original: Optional[str] = None


@dataclass
class Placement:
    entry: Entry
    row: int
    col: int
    direction: str
    number: Optional[int] = None


@dataclass
class Layout:
    cells: Dict[Tuple[int, int], str] = field(default_factory=dict)
    dirs_by_cell: Dict[Tuple[int, int], set] = field(default_factory=dict)
    placements: List[Placement] = field(default_factory=list)
    omitted: List[Entry] = field(default_factory=list)

    def clone(self) -> "Layout":
        new = Layout()
        new.cells = dict(self.cells)
        new.dirs_by_cell = {k: set(v) for k, v in self.dirs_by_cell.items()}
        new.placements = [Placement(p.entry, p.row, p.col, p.direction, p.number) for p in self.placements]
        new.omitted = list(self.omitted)
        return new

    def is_empty(self) -> bool:
        return not self.placements

    def bounds(self) -> Tuple[int, int, int, int]:
        if not self.cells:
            return 0, 0, 0, 0
        rows = [r for r, _ in self.cells]
        cols = [c for _, c in self.cells]
        return min(rows), max(rows), min(cols), max(cols)

    def width_height(self) -> Tuple[int, int]:
        if not self.cells:
            return 0, 0
        min_r, max_r, min_c, max_c = self.bounds()
        return max_c - min_c + 1, max_r - min_r + 1

    def area(self) -> int:
        w, h = self.width_height()
        return w * h

    def intersections(self) -> int:
        total = 0
        for dirs in self.dirs_by_cell.values():
            if len(dirs) > 1:
                total += 1
        return total


# ---------------------------------------------------------------------------
# Utilitaires métiers
# ---------------------------------------------------------------------------

def latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def normalize_word(word: str) -> str:
    word = str(word).strip().upper()
    word = re.sub(r"[\s\-_'’]+", "", word)
    word = "".join(ch for ch in word if ch.isalnum())
    return word


def sanitize_folder_name(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = cleaned.strip("._-")
    return cleaned or "projet_mots_croises"


def ensure_unique_directory(path: Path) -> Path:
    if not path.exists():
        return path
    base = path
    counter = 2
    while True:
        candidate = base.with_name(f"{base.name}_{counter:02d}")
        if not candidate.exists():
            return candidate
        counter += 1


def entries_from_json_data(data: object) -> List[Entry]:
    if isinstance(data, dict):
        for key in ("mots", "words", "entries"):
            if key in data:
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError(
            "Format JSON non reconnu. Attendu : une liste de mots, "
            "ou un objet contenant une clé 'mots' / 'words' / 'entries'."
        )

    entries: List[Entry] = []
    for item in data:
        if isinstance(item, str):
            word = normalize_word(item)
            if len(word) >= 2:
                entries.append(Entry(word=word, clue=None, original=item.strip()))
        elif isinstance(item, dict):
            raw_word = None
            for key in ("mot", "word", "texte", "text", "answer", "solution"):
                if key in item:
                    raw_word = item[key]
                    break
            if raw_word is None:
                continue

            word = normalize_word(str(raw_word))
            if len(word) < 2:
                continue

            clue = None
            for key in ("indice", "definition", "définition", "clue", "hint", "question"):
                if key in item and item[key]:
                    clue = str(item[key]).strip()
                    break
            entries.append(Entry(word=word, clue=clue, original=str(raw_word).strip()))

    unique: Dict[str, Entry] = {}
    for entry in entries:
        if entry.word not in unique:
            unique[entry.word] = entry

    result = list(unique.values())
    if not result:
        raise ValueError("Aucun mot exploitable trouvé dans le JSON.")
    return result


def load_entries(path: Path) -> List[Entry]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return entries_from_json_data(data)


def entries_to_json_data(entries: Sequence[Entry]) -> Dict[str, List[Dict[str, str]]]:
    payload: List[Dict[str, str]] = []
    for entry in entries:
        item = {"mot": entry.original or entry.word}
        if entry.clue:
            item["definition"] = entry.clue
        payload.append(item)
    return {"mots": payload}


def save_entries_json(entries: Sequence[Entry], path: Path) -> None:
    payload = entries_to_json_data(entries)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Moteur de placement
# ---------------------------------------------------------------------------

def iter_word_cells(word: str, row: int, col: int, direction: str) -> Iterable[Tuple[int, int, str, int]]:
    for i, ch in enumerate(word):
        if direction == H:
            yield row, col + i, ch, i
        else:
            yield row + i, col, ch, i


def before_after_cell(word: str, row: int, col: int, direction: str) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    if direction == H:
        return (row, col - 1), (row, col + len(word))
    return (row - 1, col), (row + len(word), col)


def side_neighbors(row: int, col: int, direction: str) -> List[Tuple[int, int]]:
    if direction == H:
        return [(row - 1, col), (row + 1, col)]
    return [(row, col - 1), (row, col + 1)]


def can_place(layout: Layout, word: str, row: int, col: int, direction: str) -> Tuple[bool, int]:
    if direction not in (H, V):
        return False, 0

    overlap_count = 0
    before, after = before_after_cell(word, row, col, direction)
    if before in layout.cells or after in layout.cells:
        return False, 0

    for r, c, ch, _ in iter_word_cells(word, row, col, direction):
        existing = layout.cells.get((r, c))
        if existing is not None and existing != ch:
            return False, 0

        if existing is not None:
            existing_dirs = layout.dirs_by_cell.get((r, c), set())
            if direction in existing_dirs:
                return False, 0
            overlap_count += 1
        else:
            for nr, nc in side_neighbors(r, c, direction):
                if (nr, nc) in layout.cells:
                    return False, 0

    if layout.placements and overlap_count == 0:
        return False, 0

    return True, overlap_count


def place_word(layout: Layout, entry: Entry, row: int, col: int, direction: str) -> None:
    for r, c, ch, _ in iter_word_cells(entry.word, row, col, direction):
        layout.cells[(r, c)] = ch
        layout.dirs_by_cell.setdefault((r, c), set()).add(direction)
    layout.placements.append(Placement(entry=entry, row=row, col=col, direction=direction))


def candidate_positions(layout: Layout, entry: Entry) -> List[Tuple[int, int, str, int]]:
    candidates: List[Tuple[int, int, str, int]] = []
    word = entry.word

    if layout.is_empty():
        candidates.append((0, 0, random.choice([H, V]), 0))
        return candidates

    for i, ch in enumerate(word):
        for (r, c), existing_ch in layout.cells.items():
            if existing_ch != ch:
                continue

            row_h = r
            col_h = c - i
            ok, overlaps = can_place(layout, word, row_h, col_h, H)
            if ok:
                candidates.append((row_h, col_h, H, overlaps))

            row_v = r - i
            col_v = c
            ok, overlaps = can_place(layout, word, row_v, col_v, V)
            if ok:
                candidates.append((row_v, col_v, V, overlaps))

    dedup: Dict[Tuple[int, int, str], int] = {}
    for row, col, direction, overlaps in candidates:
        key = (row, col, direction)
        dedup[key] = max(overlaps, dedup.get(key, 0))

    return [(row, col, direction, ov) for (row, col, direction), ov in dedup.items()]


def evaluate_candidate(layout: Layout, entry: Entry, row: int, col: int, direction: str, overlaps: int) -> float:
    test = layout.clone()
    place_word(test, entry, row, col, direction)
    area = test.area()
    width, height = test.width_height()
    squareness_penalty = abs(width - height)
    return overlaps * 1000 - area * 2 - squareness_penalty


def assign_numbers(layout: Layout) -> None:
    start_to_number: Dict[Tuple[int, int], int] = {}
    next_number = 1

    starts = sorted({(p.row, p.col) for p in layout.placements})

    for row, col in starts:
        starts_across = ((row, col) in layout.cells) and ((row, col - 1) not in layout.cells) and ((row, col + 1) in layout.cells)
        starts_down = ((row, col) in layout.cells) and ((row - 1, col) not in layout.cells) and ((row + 1, col) in layout.cells)
        if starts_across or starts_down:
            start_to_number[(row, col)] = next_number
            next_number += 1

    for placement in layout.placements:
        placement.number = start_to_number.get((placement.row, placement.col))


def attempt_layout(entries: Sequence[Entry], rng: random.Random) -> Layout:
    layout = Layout()
    buckets: Dict[int, List[Entry]] = {}
    for entry in entries:
        buckets.setdefault(len(entry.word), []).append(entry)

    ordered: List[Entry] = []
    for length in sorted(buckets.keys(), reverse=True):
        bucket = buckets[length][:]
        rng.shuffle(bucket)
        ordered.extend(bucket)

    for entry in ordered:
        candidates = candidate_positions(layout, entry)
        if not candidates:
            layout.omitted.append(entry)
            continue

        scored = []
        for row, col, direction, overlaps in candidates:
            score = evaluate_candidate(layout, entry, row, col, direction, overlaps)
            scored.append((score, rng.random(), row, col, direction))

        scored.sort(reverse=True)
        _, _, row, col, direction = scored[0]
        place_word(layout, entry, row, col, direction)

    assign_numbers(layout)
    return layout


def choose_best_layout(entries: Sequence[Entry], attempts: int, seed: Optional[int]) -> Layout:
    if attempts < 1:
        attempts = 1

    master_rng = random.Random(seed)
    best: Optional[Layout] = None
    best_score = -math.inf

    for _ in range(attempts):
        rng = random.Random(master_rng.randint(0, 10**9))
        layout = attempt_layout(entries, rng)

        placed = len(layout.placements)
        omitted = len(layout.omitted)
        intersections = layout.intersections()
        area = layout.area()
        score = placed * 100000 + intersections * 500 - omitted * 1000 - area

        if score > best_score:
            best_score = score
            best = layout

    if best is None:
        raise RuntimeError("Impossible de générer une grille.")
    return best


def word_start_map(layout: Layout) -> Dict[Tuple[int, int], int]:
    result: Dict[Tuple[int, int], int] = {}
    for placement in layout.placements:
        if placement.number is not None:
            result[(placement.row, placement.col)] = placement.number
    return result


def renormalize_layout(layout: Layout) -> Tuple[Layout, int, int, int, int]:
    if not layout.cells:
        return layout, 0, 0, 0, 0

    min_r, max_r, min_c, max_c = layout.bounds()
    shifted = Layout()

    for (r, c), ch in layout.cells.items():
        shifted.cells[(r - min_r, c - min_c)] = ch
        shifted.dirs_by_cell[(r - min_r, c - min_c)] = set(layout.dirs_by_cell[(r, c)])

    for placement in layout.placements:
        shifted.placements.append(
            Placement(
                entry=placement.entry,
                row=placement.row - min_r,
                col=placement.col - min_c,
                direction=placement.direction,
                number=placement.number,
            )
        )

    shifted.omitted = list(layout.omitted)
    return shifted, 0, max_r - min_r, 0, max_c - min_c


def clues_by_direction(layout: Layout) -> Tuple[List[Placement], List[Placement]]:
    across = [p for p in layout.placements if p.direction == H]
    down = [p for p in layout.placements if p.direction == V]
    across.sort(key=lambda p: (p.number or 0, p.row, p.col))
    down.sort(key=lambda p: (p.number or 0, p.row, p.col))
    return across, down


def build_word_bank(entries: Sequence[Entry]) -> List[str]:
    return sorted((entry.original or entry.word).upper() for entry in entries)


def render_grid_tikz(layout: Layout, show_solution: bool = False, cell_size_cm: float = DEFAULT_CELL_SIZE_CM) -> str:
    if not layout.cells:
        return "% Grille vide"

    _, max_r, _, max_c = renormalize_layout(layout)[1:]
    num_map = word_start_map(layout)

    lines: List[str] = []
    lines.append(r"\begin{tikzpicture}[x=" + f"{cell_size_cm:.3f}" + r"cm,y=-" + f"{cell_size_cm:.3f}" + r"cm]")
    for r in range(max_r + 1):
        for c in range(max_c + 1):
            if (r, c) in layout.cells:
                lines.append(f"  \\draw ({c},{r}) rectangle ++(1,1);")
                if (r, c) in num_map:
                    lines.append(
                        "  \\node[anchor=north west,font=\\tiny,inner sep=1pt] "
                        f"at ({c + 0.03:.2f},{r + 0.03:.2f}) {{{num_map[(r, c)]}}};"
                    )
                if show_solution:
                    ch = latex_escape(layout.cells[(r, c)])
                    lines.append(f"  \\node at ({c + 0.5:.2f},{r + 0.58:.2f}) {{\\bfseries {ch}}};")
            else:
                lines.append(f"  \\fill[pattern=north east lines, pattern color=black] ({c},{r}) rectangle ++(1,1);")
    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines)


def render_latex(layout: Layout, all_entries: Sequence[Entry], title: str = DEFAULT_TITLE, cell_size_cm: float = DEFAULT_CELL_SIZE_CM) -> str:
    layout, _, _, _, _ = renormalize_layout(layout)
    across, down = clues_by_direction(layout)
    has_clues_for_all = all(p.entry.clue for p in layout.placements)

    placed_entries = {p.entry.word for p in layout.placements}
    omitted_original = [e.original or e.word for e in all_entries if e.word not in placed_entries]
    bank = build_word_bank([p.entry for p in layout.placements])

    def format_clue(placement: Placement) -> str:
        clue = placement.entry.clue if placement.entry.clue else f"{placement.entry.original or placement.entry.word} ({len(placement.entry.word)} lettres)"
        return f"\\textbf{{{placement.number}.}} {latex_escape(clue)}"

    lines: List[str] = []
    lines.append(r"\documentclass[11pt,a4paper]{article}")
    lines.append(r"\usepackage[T1]{fontenc}")
    lines.append(r"\usepackage[utf8]{inputenc}")
    lines.append(r"\usepackage[french]{babel}")
    lines.append(r"\usepackage{lmodern}")
    lines.append(r"\usepackage{geometry}")
    lines.append(r"\usepackage{tikz}")
    lines.append(r"\usepackage{multicol}")
    lines.append(r"\usetikzlibrary{patterns}")
    lines.append(r"\geometry{margin=2cm}")
    lines.append(r"\setlength{\parindent}{0pt}")
    lines.append("")
    lines.append(r"\begin{document}")
    lines.append(r"\begin{center}")
    lines.append(r"{\LARGE \textbf{" + latex_escape(title) + r"}}\\[1em]")
    lines.append(render_grid_tikz(layout, show_solution=False, cell_size_cm=cell_size_cm))
    lines.append(r"\end{center}")
    lines.append("")

    if has_clues_for_all:
        if across:
            lines.append(r"\section*{Horizontal}")
            lines.append(r"\begin{multicols}{2}")
            for placement in across:
                lines.append(format_clue(placement) + r"\\")
            lines.append(r"\end{multicols}")
            lines.append("")
        if down:
            lines.append(r"\section*{Vertical}")
            lines.append(r"\begin{multicols}{2}")
            for placement in down:
                lines.append(format_clue(placement) + r"\\")
            lines.append(r"\end{multicols}")
            lines.append("")
    else:
        lines.append(r"\section*{Mots à placer}")
        lines.append(r"\begin{multicols}{3}")
        for word in bank:
            lines.append(latex_escape(word) + r"\\")
        lines.append(r"\end{multicols}")
        lines.append("")

    if omitted_original:
        lines.append(r"\section*{Mots non placés}")
        lines.append(latex_escape(", ".join(str(x).upper() for x in omitted_original)))
        lines.append("")

    lines.append(r"\newpage")
    lines.append(r"\section*{Solution}")
    lines.append(r"\begin{center}")
    lines.append(render_grid_tikz(layout, show_solution=True, cell_size_cm=cell_size_cm))
    lines.append(r"\end{center}")
    lines.append("")
    lines.append(r"\end{document}")
    return "\n".join(lines)


def compile_pdf(project_dir: Path, tex_filename: str = "crossword.tex") -> Tuple[bool, str]:
    if shutil.which("pdflatex") is None:
        return False, "pdflatex introuvable sur cette machine."

    try:
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", tex_filename],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return False, f"Échec de compilation PDF : {exc}"

    if result.returncode != 0:
        message = (result.stdout + "\n" + result.stderr).strip()
        message = message[-2000:] if len(message) > 2000 else message
        return False, f"Compilation LaTeX échouée.\n\n{message}"

    pdf_path = project_dir / "crossword.pdf"
    if pdf_path.exists():
        return True, f"PDF généré : {pdf_path.name}"
    return False, "La compilation semble terminée, mais le PDF n'a pas été trouvé."


# ---------------------------------------------------------------------------
# Génération projet
# ---------------------------------------------------------------------------

def generate_project(
    entries: Sequence[Entry],
    base_directory: Path,
    project_name: str,
    title: str,
    attempts: int = DEFAULT_ATTEMPTS,
    seed: Optional[int] = None,
    cell_size_cm: float = DEFAULT_CELL_SIZE_CM,
    compile_pdf_flag: bool = False,
) -> Dict[str, object]:
    if len(entries) < 2:
        raise ValueError("Il faut au moins 2 mots pour construire une grille utile.")

    cleaned_entries = []
    seen = set()
    for entry in entries:
        if len(entry.word) < 2:
            continue
        if entry.word in seen:
            continue
        seen.add(entry.word)
        cleaned_entries.append(entry)

    if len(cleaned_entries) < 2:
        raise ValueError("Après nettoyage, il ne reste pas assez de mots exploitables.")

    base_directory.mkdir(parents=True, exist_ok=True)
    project_dir = ensure_unique_directory(base_directory / sanitize_folder_name(project_name))
    project_dir.mkdir(parents=True, exist_ok=False)

    json_path = project_dir / "mots.json"
    tex_path = project_dir / "crossword.tex"
    readme_path = project_dir / "README.txt"

    layout = choose_best_layout(cleaned_entries, attempts=attempts, seed=seed)
    tex_content = render_latex(layout, cleaned_entries, title=title, cell_size_cm=cell_size_cm)

    save_entries_json(cleaned_entries, json_path)
    tex_path.write_text(tex_content, encoding="utf-8")
    readme_path.write_text(
        "Projet généré par Crossword Studio\n"
        "================================\n\n"
        f"Titre : {title}\n"
        f"Date : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Mots fournis : {len(cleaned_entries)}\n"
        f"Mots placés : {len(layout.placements)}\n"
        f"Mots non placés : {len(layout.omitted)}\n\n"
        "Fichiers :\n"
        "- mots.json : données source\n"
        "- crossword.tex : document LaTeX\n"
        "- crossword.pdf : document PDF (si compilation activée et pdflatex disponible)\n",
        encoding="utf-8",
    )

    compiled = False
    compile_message = "Compilation PDF non demandée."
    if compile_pdf_flag:
        compiled, compile_message = compile_pdf(project_dir, tex_filename=tex_path.name)

    return {
        "project_dir": project_dir,
        "json_path": json_path,
        "tex_path": tex_path,
        "layout": layout,
        "compiled": compiled,
        "compile_message": compile_message,
    }


# ---------------------------------------------------------------------------
# Interface graphique
# ---------------------------------------------------------------------------

class CrosswordStudioApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Crossword Studio — Générateur de mots croisés LaTeX")
        self.root.geometry("1320x860")
        self.root.minsize(1150, 760)

        self.entries: List[Entry] = []
        self.current_json_path: Optional[Path] = None
        self.selected_index: Optional[int] = None
        self.last_project_dir: Optional[Path] = None
        self._folder_name_autofill = True

        self._configure_style()
        self._build_variables()
        self._build_ui()
        self._populate_defaults()
        self.refresh_table()
        self.root.bind("<Control-Return>", lambda _event: self.generate_from_ui())
        self.root.bind("<Return>", self._handle_return_key)

    def _configure_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        bg = "#f5f7fb"
        card = "#ffffff"
        accent = "#184e77"
        muted = "#5f6b7a"

        self.root.configure(bg=bg)

        style.configure("Root.TFrame", background=bg)
        style.configure("Card.TFrame", background=card)
        style.configure("Card.TLabelframe", background=card)
        style.configure("Card.TLabelframe.Label", background=card, foreground="#12344d", font=("Segoe UI", 11, "bold"))
        style.configure("HeaderTitle.TLabel", background=bg, foreground="#12344d", font=("Segoe UI", 22, "bold"))
        style.configure("HeaderSub.TLabel", background=bg, foreground=muted, font=("Segoe UI", 10))
        style.configure("Section.TLabel", background=card, foreground="#12344d", font=("Segoe UI", 10, "bold"))
        style.configure("Value.TLabel", background=card, foreground="#1f2933", font=("Segoe UI", 10))
        style.configure("Info.TLabel", background=card, foreground=muted, font=("Segoe UI", 9))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 9))
        style.configure("Hero.TButton", font=("Segoe UI", 14, "bold"), padding=(18, 18))
        style.configure("TButton", padding=(10, 8), font=("Segoe UI", 10))
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", accent)], foreground=[("!disabled", "white")])

    def _build_variables(self) -> None:
        default_base = Path.home() / "Documents"
        if not default_base.exists():
            default_base = Path.home()

        self.title_var = tk.StringVar(value=DEFAULT_TITLE)
        self.attempts_var = tk.IntVar(value=DEFAULT_ATTEMPTS)
        self.seed_var = tk.StringVar(value="")
        self.base_dir_var = tk.StringVar(value=str(default_base))
        self.project_name_var = tk.StringVar(value=self._default_project_name(DEFAULT_TITLE))
        self.compile_pdf_var = tk.BooleanVar(value=False)
        self.source_var = tk.StringVar(value="Aucune source JSON importée")
        self.status_var = tk.StringVar(value="Prêt")
        self.editor_mode_var = tk.StringVar(value="Mode création · ajoutez un nouveau mot")
        self.word_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")

        self.title_var.trace_add("write", self._on_title_change)
        self.project_name_var.trace_add("write", self._on_project_name_change)
        self.search_var.trace_add("write", lambda *_: self.refresh_table())

    def _build_ui(self) -> None:
        root = ttk.Frame(self.root, style="Root.TFrame", padding=18)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root, style="Root.TFrame")
        header.pack(fill="x", pady=(0, 14))

        ttk.Label(header, text="Crossword Studio", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Importez un fichier JSON ou saisissez vos mots et définitions, puis exportez un projet LaTeX propre dans un dossier dédié.",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        content = ttk.Panedwindow(root, orient="horizontal")
        content.pack(fill="both", expand=True)

        left = ttk.Frame(content, style="Root.TFrame")
        right = ttk.Frame(content, style="Root.TFrame")
        content.add(left, weight=5)
        content.add(right, weight=4)

        self._build_left_panel(left)
        self._build_right_panel(right)

        footer = ttk.Frame(root, style="Root.TFrame")
        footer.pack(fill="x", pady=(12, 0))
        ttk.Label(footer, textvariable=self.status_var, style="HeaderSub.TLabel").pack(side="left")
        ttk.Button(footer, text="Ouvrir le dossier généré", command=self.open_last_project_dir).pack(side="right")
        ttk.Button(footer, text="Quitter", command=self.root.destroy).pack(side="right", padx=(10, 0))

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        source_card = ttk.LabelFrame(parent, text="1. Source des données", style="Card.TLabelframe", padding=14)
        source_card.pack(fill="x", padx=(0, 8), pady=(0, 12))

        buttons = ttk.Frame(source_card, style="Card.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Importer un JSON…", command=self.import_json).pack(side="left")
        ttk.Button(buttons, text="Exporter le JSON courant…", command=self.export_current_json).pack(side="left", padx=8)
        ttk.Button(buttons, text="Vider la liste", command=self.clear_entries).pack(side="left")

        ttk.Label(source_card, textvariable=self.source_var, style="Info.TLabel").pack(anchor="w", pady=(10, 0))

        editor_card = ttk.LabelFrame(parent, text="2. Saisie / édition des mots", style="Card.TLabelframe", padding=14)
        editor_card.pack(fill="both", expand=True, padx=(0, 8))

        form = ttk.Frame(editor_card, style="Card.TFrame")
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Mot", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        ttk.Entry(form, textvariable=self.word_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(form, text="Définition / indice", style="Section.TLabel").grid(row=1, column=0, sticky="nw", padx=(0, 10))
        self.clue_text = ScrolledText(form, height=4, wrap="word", font=("Segoe UI", 10))
        self.clue_text.grid(row=1, column=1, sticky="ew")

        ttk.Label(editor_card, textvariable=self.editor_mode_var, style="Info.TLabel").pack(anchor="w", pady=(12, 0))

        actions = ttk.Frame(editor_card, style="Card.TFrame")
        actions.pack(fill="x", pady=(10, 10))
        self.add_button = ttk.Button(actions, text="Ajouter le mot", command=self.add_entry)
        self.add_button.pack(side="left")
        self.load_button = ttk.Button(actions, text="Charger la sélection", command=self.load_selected_entry)
        self.load_button.pack(side="left", padx=8)
        self.save_button = ttk.Button(actions, text="Enregistrer la modification", command=self.save_selected_entry)
        self.save_button.pack(side="left")
        self.delete_button = ttk.Button(actions, text="Supprimer", command=self.delete_selected_entry)
        self.delete_button.pack(side="left", padx=8)
        self.new_button = ttk.Button(actions, text="Nouveau", command=self.reset_editor)
        self.new_button.pack(side="left")

        search_row = ttk.Frame(editor_card, style="Card.TFrame")
        search_row.pack(fill="x", pady=(2, 8))
        ttk.Label(search_row, text="Filtrer", style="Section.TLabel").pack(side="left", padx=(0, 10))
        ttk.Entry(search_row, textvariable=self.search_var).pack(side="left", fill="x", expand=True)

        columns = ("mot", "definition", "longueur")
        self.tree = ttk.Treeview(editor_card, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("mot", text="Mot")
        self.tree.heading("definition", text="Définition")
        self.tree.heading("longueur", text="Lettres")
        self.tree.column("mot", width=180, anchor="w")
        self.tree.column("definition", width=430, anchor="w")
        self.tree.column("longueur", width=80, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.on_tree_select())
        self.tree.bind("<Double-1>", lambda _: self.load_selected_entry())

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        settings_card = ttk.LabelFrame(parent, text="3. Paramètres du projet", style="Card.TLabelframe", padding=14)
        settings_card.pack(fill="x", padx=(8, 0), pady=(0, 12))
        settings_card.columnconfigure(1, weight=1)

        ttk.Label(settings_card, text="Titre du document", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        ttk.Entry(settings_card, textvariable=self.title_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(settings_card, text="Qualité de recherche", style="Section.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        ttk.Spinbox(settings_card, from_=50, to=3000, increment=50, textvariable=self.attempts_var).grid(row=1, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(settings_card, text="Graine aléatoire", style="Section.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        ttk.Entry(settings_card, textvariable=self.seed_var).grid(row=2, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(settings_card, text="Dossier parent", style="Section.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        dir_row = ttk.Frame(settings_card, style="Card.TFrame")
        dir_row.grid(row=3, column=1, sticky="ew", pady=(0, 8))
        dir_row.columnconfigure(0, weight=1)
        ttk.Entry(dir_row, textvariable=self.base_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(dir_row, text="Parcourir…", command=self.choose_output_directory).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(settings_card, text="Nom du sous-dossier", style="Section.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        folder_row = ttk.Frame(settings_card, style="Card.TFrame")
        folder_row.grid(row=4, column=1, sticky="ew", pady=(0, 8))
        folder_row.columnconfigure(0, weight=1)
        ttk.Entry(folder_row, textvariable=self.project_name_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(folder_row, text="Renommer", command=self.regenerate_project_name).grid(row=0, column=1, padx=(8, 0))

        ttk.Checkbutton(settings_card, text="Compiler aussi le PDF si pdflatex est disponible", variable=self.compile_pdf_var).grid(row=5, column=0, columnspan=2, sticky="w")

        summary_card = ttk.LabelFrame(parent, text="4. Résumé", style="Card.TLabelframe", padding=14)
        summary_card.pack(fill="x", padx=(8, 0), pady=(0, 12))

        self.summary_label = ttk.Label(summary_card, text="", style="Value.TLabel", justify="left")
        self.summary_label.pack(anchor="w")

        preview_card = ttk.LabelFrame(parent, text="Journal", style="Card.TLabelframe", padding=14)
        preview_card.pack(fill="both", expand=True, padx=(8, 0))

        self.log_text = ScrolledText(preview_card, height=18, wrap="word", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

        action_bar = ttk.Frame(parent, style="Root.TFrame")
        action_bar.pack(fill="x", padx=(8, 0), pady=(12, 0))
        self.generate_button = tk.Button(
            action_bar,
            text="GÉNÉRER LE PROJET",
            command=self.generate_from_ui,
            font=("Segoe UI", 16, "bold"),
            bg="#184e77",
            fg="white",
            activebackground="#12344d",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=22,
            pady=18,
            cursor="hand2",
        )
        self.generate_button.pack(side="left", fill="x", expand=True)

    def _handle_return_key(self, event: tk.Event) -> None:
        widget_name = str(getattr(event, "widget", ""))
        if "text" in widget_name.lower():
            return
        if self.selected_index is not None:
            self.save_selected_entry()
        else:
            self.add_entry()

    def _populate_defaults(self) -> None:
        self.log("Application prête. Importez un fichier JSON ou ajoutez vos mots manuellement.")
        self.update_summary()
        self.update_editor_buttons()

    def _default_project_name(self, title: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{sanitize_folder_name(title)}_{timestamp}"

    def _on_title_change(self, *_args: object) -> None:
        if self._folder_name_autofill:
            self.project_name_var.set(self._default_project_name(self.title_var.get() or DEFAULT_TITLE))
        self.update_summary()

    def _on_project_name_change(self, *_args: object) -> None:
        self.update_summary()

    def regenerate_project_name(self) -> None:
        self._folder_name_autofill = True
        self.project_name_var.set(self._default_project_name(self.title_var.get() or DEFAULT_TITLE))

    def choose_output_directory(self) -> None:
        directory = filedialog.askdirectory(title="Choisir le dossier parent d'export")
        if directory:
            self.base_dir_var.set(directory)
            self.update_summary()

    def import_json(self) -> None:
        path = filedialog.askopenfilename(
            title="Choisir un fichier JSON",
            filetypes=[("Fichiers JSON", "*.json"), ("Tous les fichiers", "*.*")],
        )
        if not path:
            return

        try:
            entries = load_entries(Path(path))
        except Exception as exc:
            messagebox.showerror("Import JSON", f"Impossible de lire le fichier :\n\n{exc}")
            return

        self.entries = list(entries)
        self.current_json_path = Path(path)
        self.source_var.set(f"Source : {path}")
        self.reset_editor(clear_selection=True)
        self.refresh_table()
        self.log(f"JSON importé : {path}")
        self.log(f"{len(entries)} mots chargés.")
        self.set_status("JSON importé avec succès")

    def export_current_json(self) -> None:
        if not self.entries:
            messagebox.showwarning("Export JSON", "Il n'y a aucun mot à exporter.")
            return

        path = filedialog.asksaveasfilename(
            title="Exporter les données en JSON",
            defaultextension=".json",
            filetypes=[("Fichiers JSON", "*.json")],
            initialfile="mots.json",
        )
        if not path:
            return

        try:
            save_entries_json(self.entries, Path(path))
        except Exception as exc:
            messagebox.showerror("Export JSON", f"Impossible d'écrire le fichier :\n\n{exc}")
            return

        self.log(f"JSON exporté : {path}")
        self.set_status("JSON exporté")

    def clear_entries(self) -> None:
        if self.entries and not messagebox.askyesno("Vider la liste", "Supprimer tous les mots actuellement chargés ?"):
            return
        self.entries = []
        self.current_json_path = None
        self.source_var.set("Aucune source JSON importée")
        self.reset_editor(clear_selection=True)
        self.refresh_table()
        self.log("Liste vidée.")
        self.set_status("Liste vidée")

    def get_clue_text(self) -> str:
        return self.clue_text.get("1.0", "end").strip()

    def set_clue_text(self, value: str) -> None:
        self.clue_text.delete("1.0", "end")
        self.clue_text.insert("1.0", value)

    def reset_editor(self, clear_selection: bool = False) -> None:
        self.word_var.set("")
        self.set_clue_text("")
        self.selected_index = None
        self.editor_mode_var.set("Mode création · ajoutez un nouveau mot")
        if clear_selection:
            for item in self.tree.selection():
                self.tree.selection_remove(item)
        self.update_editor_buttons()

    def current_filtered_entries(self) -> List[Tuple[int, Entry]]:
        query = self.search_var.get().strip().lower()
        indexed_entries = list(enumerate(self.entries))
        if not query:
            return indexed_entries
        filtered = []
        for idx, entry in indexed_entries:
            haystack = f"{entry.original or entry.word} {entry.clue or ''}".lower()
            if query in haystack:
                filtered.append((idx, entry))
        return filtered

    def refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        for idx, entry in self.current_filtered_entries():
            clue = entry.clue or ""
            short_clue = clue if len(clue) <= 90 else clue[:87] + "..."
            self.tree.insert("", "end", iid=str(idx), values=(entry.original or entry.word, short_clue, len(entry.word)))

        self.update_summary()
        self.update_editor_buttons()

    def update_editor_buttons(self) -> None:
        editing = self.selected_index is not None
        if hasattr(self, "save_button"):
            self.save_button.state(["!disabled"] if editing else ["disabled"])
        if hasattr(self, "delete_button"):
            tree_has_selection = bool(self.tree.selection()) if hasattr(self, "tree") else False
            self.delete_button.state(["!disabled"] if tree_has_selection else ["disabled"])
        if hasattr(self, "load_button"):
            tree_has_selection = bool(self.tree.selection()) if hasattr(self, "tree") else False
            self.load_button.state(["!disabled"] if tree_has_selection else ["disabled"])

    def on_tree_select(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self.selected_index = None
            self.editor_mode_var.set("Mode création · ajoutez un nouveau mot")
            self.update_editor_buttons()
            return
        try:
            self.selected_index = int(selection[0])
        except ValueError:
            self.selected_index = None
            self.update_editor_buttons()
            return
        self.load_selected_entry(silent=True)

    def load_selected_entry(self, silent: bool = False) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Sélection", "Sélectionnez une ligne dans la liste.")
            return
        idx = int(selection[0])
        entry = self.entries[idx]
        self.selected_index = idx
        self.word_var.set(entry.original or entry.word)
        self.set_clue_text(entry.clue or "")
        self.editor_mode_var.set(f"Mode édition · ligne sélectionnée : {entry.original or entry.word}")
        self.update_editor_buttons()
        if not silent:
            self.log(f"Mot chargé dans l'éditeur : {entry.original or entry.word}")

    def _build_entry_from_editor(self) -> Entry:
        raw_word = self.word_var.get().strip()
        clue = self.get_clue_text() or None
        normalized = normalize_word(raw_word)

        if len(normalized) < 2:
            raise ValueError("Le mot doit contenir au moins 2 caractères alphanumériques.")

        return Entry(word=normalized, clue=clue, original=raw_word)

    def add_entry(self) -> None:
        try:
            new_entry = self._build_entry_from_editor()
        except Exception as exc:
            messagebox.showwarning("Validation", str(exc))
            return

        duplicate_idx = None
        for idx, entry in enumerate(self.entries):
            if entry.word == new_entry.word:
                duplicate_idx = idx
                break

        if duplicate_idx is not None:
            messagebox.showwarning("Validation", "Ce mot existe déjà dans la liste. Chargez-le puis utilisez « Enregistrer la modification »." )
            return

        self.entries.append(new_entry)
        self.log(f"Mot ajouté : {new_entry.original or new_entry.word}")
        self.set_status("Mot ajouté")
        self.reset_editor(clear_selection=True)
        self.refresh_table()

    def save_selected_entry(self) -> None:
        if self.selected_index is None:
            messagebox.showinfo("Modification", "Sélectionnez d'abord une ligne à modifier dans la liste.")
            return

        try:
            new_entry = self._build_entry_from_editor()
        except Exception as exc:
            messagebox.showwarning("Validation", str(exc))
            return

        duplicate_idx = None
        for idx, entry in enumerate(self.entries):
            if entry.word == new_entry.word:
                duplicate_idx = idx
                break

        if duplicate_idx is not None and duplicate_idx != self.selected_index:
            messagebox.showwarning("Validation", "Ce mot existe déjà dans la liste.")
            return

        self.entries[self.selected_index] = new_entry
        self.log(f"Mot modifié : {new_entry.original or new_entry.word}")
        self.set_status("Mot modifié")
        self.reset_editor(clear_selection=True)
        self.refresh_table()

    def delete_selected_entry(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Sélection", "Sélectionnez un mot à supprimer.")
            return

        idx = int(selection[0])
        entry = self.entries[idx]
        if not messagebox.askyesno("Suppression", f"Supprimer le mot « {entry.original or entry.word} » ?"):
            return

        del self.entries[idx]
        self.reset_editor(clear_selection=True)
        self.refresh_table()
        self.log(f"Mot supprimé : {entry.original or entry.word}")
        self.set_status("Mot supprimé")
        self.update_editor_buttons()

    def open_last_project_dir(self) -> None:
        if self.last_project_dir is None or not self.last_project_dir.exists():
            messagebox.showinfo("Dossier projet", "Aucun dossier généré n'est encore disponible.")
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(self.last_project_dir)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.last_project_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self.last_project_dir)])
        except Exception as exc:
            messagebox.showerror("Dossier projet", f"Impossible d'ouvrir le dossier :\n\n{exc}")

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def update_summary(self) -> None:
        total = len(self.entries)
        with_clues = sum(1 for e in self.entries if e.clue)
        lengths = [len(e.word) for e in self.entries]
        average = (sum(lengths) / len(lengths)) if lengths else 0.0
        folder_preview = Path(self.base_dir_var.get() or ".") / sanitize_folder_name(self.project_name_var.get())

        self.summary_label.configure(
            text=(
                f"Mots chargés : {total}\n"
                f"Avec définition : {with_clues}\n"
                f"Longueur moyenne : {average:.1f} lettres\n"
                f"Titre : {self.title_var.get() or DEFAULT_TITLE}\n"
                f"Dossier projet : {folder_preview}"
            )
        )

    def _parse_seed(self) -> Optional[int]:
        raw = self.seed_var.get().strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError("La graine aléatoire doit être un entier.") from exc

    def generate_from_ui(self) -> None:
        if not self.entries:
            messagebox.showwarning("Génération", "Ajoutez au moins quelques mots avant de générer le projet.")
            return

        try:
            base_dir = Path(self.base_dir_var.get().strip()).expanduser()
            project_name = self.project_name_var.get().strip() or self._default_project_name(self.title_var.get() or DEFAULT_TITLE)
            seed = self._parse_seed()
            attempts = int(self.attempts_var.get())

            result = generate_project(
                entries=self.entries,
                base_directory=base_dir,
                project_name=project_name,
                title=self.title_var.get().strip() or DEFAULT_TITLE,
                attempts=attempts,
                seed=seed,
                compile_pdf_flag=self.compile_pdf_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("Génération", f"La génération a échoué :\n\n{exc}")
            self.log(f"Erreur : {exc}")
            self.set_status("Échec de génération")
            return

        project_dir: Path = result["project_dir"]  # type: ignore[assignment]
        tex_path: Path = result["tex_path"]  # type: ignore[assignment]
        json_path: Path = result["json_path"]  # type: ignore[assignment]
        layout: Layout = result["layout"]  # type: ignore[assignment]
        compiled: bool = bool(result["compiled"])
        compile_message: str = str(result["compile_message"])

        self.last_project_dir = project_dir
        self.log(f"Projet créé : {project_dir}")
        self.log(f"- JSON : {json_path.name}")
        self.log(f"- TEX  : {tex_path.name}")
        self.log(f"- Mots placés : {len(layout.placements)}/{len(self.entries)}")
        if layout.omitted:
            self.log("- Non placés : " + ", ".join(entry.word for entry in layout.omitted))
        self.log(f"- {compile_message}")
        self.set_status("Projet généré avec succès")

        message = (
            f"Projet généré avec succès dans :\n\n{project_dir}\n\n"
            f"Mots placés : {len(layout.placements)} / {len(self.entries)}\n"
            f"Mots non placés : {len(layout.omitted)}\n\n"
            f"{compile_message}"
        )
        if compiled:
            messagebox.showinfo("Génération terminée", message)
        else:
            messagebox.showwarning("Génération terminée", message)


def launch_gui() -> int:
    if tk is None:
        print("Tkinter n'est pas disponible dans cet environnement.", file=sys.stderr)
        return 1
    try:
        root = tk.Tk()
    except Exception as exc:
        print(f"Impossible de démarrer l'interface graphique : {exc}", file=sys.stderr)
        return 1
    app = CrosswordStudioApp(root)
    root.mainloop()
    return 0


# ---------------------------------------------------------------------------
# Mode ligne de commande
# ---------------------------------------------------------------------------

def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Génère une grille de mots croisés LaTeX à partir d'un fichier JSON")
    parser.add_argument("input_json", help="Fichier JSON contenant les mots")
    parser.add_argument("output_tex", nargs="?", default="crossword.tex", help="Fichier .tex de sortie")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS, help=f"Nombre d'essais de placement (défaut: {DEFAULT_ATTEMPTS})")
    parser.add_argument("--seed", type=int, default=None, help="Graine aléatoire pour un résultat reproductible")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="Titre du document LaTeX")
    parser.add_argument("--cell-size", type=float, default=DEFAULT_CELL_SIZE_CM, help="Taille d'une case en cm dans le document LaTeX")
    return parser


def main_cli(argv: Sequence[str]) -> int:
    parser = build_cli_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input_json)
    output_path = Path(args.output_tex)

    try:
        entries = load_entries(input_path)
        layout = choose_best_layout(entries, attempts=args.attempts, seed=args.seed)
        tex = render_latex(layout, entries, title=args.title, cell_size_cm=args.cell_size)
        output_path.write_text(tex, encoding="utf-8")

        placed = len(layout.placements)
        total = len(entries)
        print(f"OK : {placed}/{total} mots placés.")
        print(f"Fichier écrit : {output_path}")
        if layout.omitted:
            print("Mots non placés :", ", ".join(e.word for e in layout.omitted))
        return 0
    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        return main_cli(argv)
    return launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
