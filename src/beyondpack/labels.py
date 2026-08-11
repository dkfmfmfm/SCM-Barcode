from __future__ import annotations

import html


def render_group_label(group: dict, items: list[dict], box_number: int) -> str:
    def country_label(item: dict) -> str:
        code = str(item["country_code"])
        name = str(item.get("country_name") or code)
        return code if name.upper() == code.upper() else f"{name} ({code})"

    item_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item['fnsku']))}</td>"
        f"<td>{html.escape(str(item['item_code']))}</td>"
        f"<td>{html.escape(str(item['sku']))}</td>"
        f"<td>{html.escape(country_label(item))}</td>"
        f"<td>{int(item['qty_per_box'])}</td>"
        "</tr>"
        for item in items
    )
    return f"""
    <html><head><meta charset="utf-8"><style>
      body {{ font-family: 'Malgun Gothic', sans-serif; margin: 8mm; color: #111; }}
      h1 {{ font-size: 22pt; margin: 0 0 4mm; }}
      .meta {{ font-size: 12pt; margin-bottom: 3mm; }}
      table {{ border-collapse: collapse; width: 100%; font-size: 10pt; }}
      th, td {{ border: 1px solid #222; padding: 2mm; text-align: left; }}
      th {{ background: #eee; }}
    </style></head><body>
      <h1>BOX {box_number}</h1>
      <div class="meta">
        {group['weight_kg']} kg · {group['length_cm']} × {group['width_cm']} × {group['height_cm']} cm
      </div>
      <table><thead><tr><th>FNSKU</th><th>품목코드</th><th>SKU</th><th>국가</th><th>EA</th></tr></thead>
      <tbody>{item_rows}</tbody></table>
    </body></html>
    """
