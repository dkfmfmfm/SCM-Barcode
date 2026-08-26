from __future__ import annotations

import html


# 한 라벨에 표시하는 최대 구성품 행. 이 수를 넘으면 남은 건수를 요약해
# 박스 1개당 라벨이 정확히 1장으로 유지되도록 한다.
MAX_ITEM_ROWS = 6

# 인쇄영역 높이가 이 값보다 낮으면 표를 넣지 않고 박스번호 중심으로 압축한다.
# 40x25mm 같은 소형 라벨에서 표를 넣으면 글자가 읽을 수 없을 만큼 작아진다.
COMPACT_HEIGHT_MM = 40.0


def _country_label(item: dict) -> str:
    code = str(item["country_code"])
    name = str(item.get("country_name") or code)
    return code if name.upper() == code.upper() else f"{name} ({code})"


def _item_rows(items: list[dict]) -> str:
    visible = items[:MAX_ITEM_ROWS]
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item['fnsku']))}</td>"
        f"<td>{html.escape(str(item['item_code']))}</td>"
        f"<td>{html.escape(str(item['sku']))}</td>"
        f"<td>{html.escape(_country_label(item))}</td>"
        f"<td align=\"right\">{int(item['qty_per_box'])}</td>"
        "</tr>"
        for item in visible
    )
    hidden = len(items) - len(visible)
    if hidden > 0:
        rows += f"<tr><td colspan=\"5\">외 {hidden}개 품목 · 포장실적 Excel 참조</td></tr>"
    return rows


def _summary(group: dict, items: list[dict]) -> str:
    """소형 라벨용 한 줄 요약.

    단품이면 FNSKU와 수량, 합포면 품목 수를 쓴다. 작업자가 스캔하는 값이
    FNSKU이므로 라벨에도 같은 값을 실어야 박스와 실물을 바로 대조할 수 있다.
    """
    if len(items) == 1:
        item = items[0]
        head = f"{item['fnsku']} · {int(item['qty_per_box'])}EA"
    else:
        total = sum(int(item["qty_per_box"]) for item in items)
        head = f"합포 {len(items)}품목 · {total}EA"
    return html.escape(f"{head} · {group['weight_kg']}kg")


def box_numbers(start_no: int, count: int) -> tuple[int, ...]:
    """박스 시작번호와 박스수량으로 라벨에 인쇄할 번호를 만든다.

    박스수량 N이면 `start_no`부터 1씩 증가하는 N개의 번호가 나오며, 이 번호가
    라벨 1장씩과 정확히 1:1로 대응한다.
    """
    total = max(1, int(count))
    first = int(start_no)
    return tuple(first + offset for offset in range(total))


def render_group_label(
    group: dict,
    items: list[dict],
    box_number: int,
    box_total: int = 0,
    compact: bool = False,
) -> str:
    """박스 라벨 1장을 HTML로 반환한다. 박스번호는 `#번호`로 크게 인쇄한다."""
    sequence = f"{box_number} / {box_total}" if box_total else str(box_number)
    shipment = html.escape(str(group.get("shipment_code") or "")).strip()
    style = (
        "<style>"
        "body { font-family: 'Malgun Gothic', sans-serif; margin: 0; color: #000; }"
        "th { background: #eee; }"
        "</style>"
    )
    if compact:
        body = f"""
      <table width="100%" border="0" cellspacing="0" cellpadding="0">
        <tr>
          <td><span style="font-size:8pt; font-weight:800;">{shipment}</span></td>
          <td align="right"><span style="font-size:8pt;">{sequence}</span></td>
        </tr>
      </table>
      <div style="font-size:27pt; font-weight:900; margin:0;">#{box_number}</div>
      <div style="font-size:7pt;">{_summary(group, items)}</div>
        """
    else:
        shipment_line = (
            f'<div style="font-size:11pt; font-weight:700;">{shipment}</div>'
            if shipment
            else ""
        )
        body = f"""
      <table width="100%" border="0" cellspacing="0" cellpadding="0">
        <tr>
          <td><span style="font-size:30pt; font-weight:900;">#{box_number}</span></td>
          <td align="right">{shipment_line}
            <div style="font-size:9pt;">박스 {sequence}</div></td>
        </tr>
      </table>
      <div style="font-size:9pt;">
        {group['weight_kg']} kg &middot;
        {group['length_cm']} &times; {group['width_cm']} &times; {group['height_cm']} cm
      </div>
      <table border="1" width="100%" cellspacing="0" cellpadding="1"
             style="font-size:7pt; border-collapse: collapse;">
        <thead>
          <tr><th>FNSKU</th><th>품목코드</th><th>SKU</th><th>국가</th><th>EA</th></tr>
        </thead>
        <tbody>{_item_rows(items)}</tbody>
      </table>
        """
    return f'<html><head><meta charset="utf-8">{style}</head><body>{body}</body></html>'
