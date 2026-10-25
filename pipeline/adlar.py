"""Ad sözlüğü eşleştirmesi: aksansız metinde Türkçe kişi ve kurum adlarını bulur.

Bu modül **yaprak**tır: yalnızca pandas'a bağlıdır. Hem sözlüğü kuran `pipeline/entities.py` hem panel
(`app.py`) buradan okur; eşleştirme kuralı tek yerdedir.

Neden gerekli: GDELT Türkçe haberleri İngilizceye makine çevirisiyle çevirip adları öyle çıkarır. Bu
yüzden GDELT'in kişi/kurum alanları Türkçede bozuktur ("Akın" → "Flock", soyadlar yer adı sanılır).
Sözlük, Türkçe başlıklardan kurulmuş temiz bir ad listesidir (`pipeline/entities.py sozluk`).
"""
import collections
import re

import pandas as pd

# Adres ve başlık metninde son kelimeye eklenebilen Türkçe ekler (aksansız)
SUFFIXES = {
    "i", "u", "e", "a", "yi", "yu", "ye", "ya", "in", "un", "nin", "nun", "de", "da", "te", "ta", "den", "dan", "ten",
    "tan", "le", "la", "yle", "yla", "ler", "lar", "leri", "lari", "li", "lu", "ci", "cu", "nden", "ndan", "ne",
    "na", "nde", "nda", "dir", "dur", "tir", "tur", "ydi", "ydu", "lik", "luk", "ce", "ca", "ye", "ile", "yle",
    "nin", "nda", "sini", "sinin", "sine", "sinde", "sinden", "si", "su", "ni", "nu",
}


class Matcher:
    """Aksansız metinde sözlük adlarını bulur. Son kelimede Türkçe ek tolere edilir ("ozelden" → "ozel")."""

    def __init__(self, gz: pd.DataFrame):
        self.by_first = collections.defaultdict(list)
        for ad, key, tur in gz[["ad", "anahtar", "tur"]].itertuples(index=False):
            toks = tuple(key.split())
            if toks:
                self.by_first[toks[0]].append((toks, ad, tur))
        for v in self.by_first.values():
            v.sort(key=lambda x: -len(x[0]))  # uzun ad önce ("aziz ihsan aktas" > "ihsan aktas")

    @staticmethod
    def _tok_match(tok: str, want: str) -> bool:
        return tok == want or (tok.startswith(want) and len(want) >= 3 and tok[len(want):] in SUFFIXES)

    def find(self, folded: str) -> list:
        toks = re.findall(r"[a-z0-9]+", folded)
        out, i = [], 0
        while i < len(toks):
            tok, hit = toks[i], None
            cands = self.by_first.get(tok, [])
            if not cands:  # tek kelimelik ad + ek ("chpnin")
                for k in range(1, 7):
                    if len(tok) - k >= 2 and tok[-k:] in SUFFIXES:
                        cands = [c for c in self.by_first.get(tok[:-k], []) if len(c[0]) == 1]
                        if cands:
                            break
            for name_toks, ad, tur in cands:
                n = len(name_toks)
                if n == 1:
                    hit = (1, ad, tur)
                    break
                seg = toks[i:i + n]
                if len(seg) == n and seg[:-1] == list(name_toks[:-1]) and self._tok_match(seg[-1], name_toks[-1]):
                    hit = (n, ad, tur)
                    break
            if hit:
                out.append((hit[1], hit[2]))
                i += hit[0]
            else:
                i += 1
        return list(dict.fromkeys(out))
