// Copyright (c) 2026, Nasiya365 and contributors
// For license information, please see license.txt

/**
 * График платежей: показываем, когда взнос был оплачен на самом деле.
 *
 *   • маркер «задним числом» у статуса, если платёж вносили позже даты факта;
 *   • подсказка при наведении с деталями платежа.
 *
 * Колонка «Дата оплаты» включена через in_list_view в installment_schedule.json.
 * Новых полей в базе не заводим: всё выводится из paid_date строки графика
 * и даты создания связанного платежа.
 */

frappe.ui.form.on('Installment Plan', {
    refresh: function (frm) {
        const grid = frm.fields_dict.schedule && frm.fields_dict.schedule.grid;
        if (!grid) return;

        bind_schedule_tooltip(frm, grid);

        // Маркер и подсказка знают «когда внесли» только из платежей — грузим пакетом.
        load_payment_meta(frm, function () {
            set_status_marker(frm, grid);
            grid.refresh();
        });
    },
});

/** Один запрос на все платежи графика — чтобы подсказка открывалась мгновенно. */
function load_payment_meta(frm, done) {
    frm.__pay_meta = frm.__pay_meta || {};

    const names = [...new Set(
        (frm.doc.schedule || []).map(r => r.payment_transaction).filter(Boolean)
    )].filter(n => !frm.__pay_meta[n]);

    if (!names.length) {
        done();
        return;
    }

    frappe.call({
        method: 'frappe.client.get_list',
        args: {
            doctype: 'Payment Transaction',
            filters: [['name', 'in', names]],
            fields: ['name', 'payment_date', 'creation'],
            limit_page_length: 0,
        },
        callback: function (r) {
            (r.message || []).forEach(function (p) {
                frm.__pay_meta[p.name] = p;
            });
            done();
        },
    });
}

/** Дата, когда платёж физически внесли в систему (YYYY-MM-DD). */
function entered_on(frm, row) {
    if (!row || !row.payment_transaction) return null;
    const meta = (frm.__pay_meta || {})[row.payment_transaction];
    if (!meta || !meta.creation) return null;
    return String(meta.creation).split(' ')[0];
}

/** Платёж внесли позже, чем клиент реально заплатил. */
function is_backdated(frm, row) {
    if (!row || !row.paid_date) return false;
    const entered = entered_on(frm, row);
    return !!entered && entered > String(row.paid_date);
}

/**
 * Компактный маркер у статуса.
 *
 * Форматтер обязан лежать в frappe.meta.docfield_map — строки грида берут
 * docfields именно оттуда, своя копия у grid.docfields не подхватывается.
 * Маркер — точка, а не текст: колонка статуса узкая, длинная подпись обрезается.
 */
function set_status_marker(frm, grid) {
    const canonical = frappe.meta.docfield_map
        && frappe.meta.docfield_map['Installment Schedule']
        && frappe.meta.docfield_map['Installment Schedule'].status;
    if (!canonical || canonical.__nasiya_marked) return;
    canonical.__nasiya_marked = true;

    const formatter = function (value, docfield, options, doc) {
        const text = frappe.utils.escape_html(value == null ? '' : String(value));
        if (doc && is_backdated(frm, doc)) {
            return text + ' <span class="indicator orange" title="' +
                frappe.utils.escape_html(__('Внесён задним числом')) + '"></span>';
        }
        return text;
    };

    canonical.formatter = formatter;
    try {
        grid.update_docfield_property('status', 'formatter', formatter);
    } catch (e) {
        // Не критично — канонический docfield уже обновлён.
    }
}

/** Текст подсказки для строки графика. */
function row_hint(frm, row) {
    const d = v => (v ? frappe.datetime.str_to_user(v) : '—');

    if (!flt(row.paid_amount)) {
        return { main: __('Не оплачен') + ' · ' + __('срок') + ' ' + d(row.due_date), extra: null };
    }

    const parts = [
        row.paid_date ? __('Оплачен') + ' ' + d(row.paid_date) : __('Частично оплачен'),
        format_currency(flt(row.paid_amount), 'USD'),
    ];
    if (row.payment_transaction) parts.push(row.payment_transaction);

    let extra = null;
    if (is_backdated(frm, row)) {
        extra = __('внесён') + ' ' + d(entered_on(frm, row)) + ', ' + __('задним числом');
    }
    return { main: parts.join(' · '), extra: extra };
}

function bind_schedule_tooltip(frm, grid) {
    if (grid.__nasiya_tip_bound) return;
    grid.__nasiya_tip_bound = true;

    inject_tooltip_css();
    const $wrap = $(grid.wrapper);

    $wrap.on('mouseenter', '.grid-row', function () {
        const name = $(this).attr('data-name');
        const row = (frm.doc.schedule || []).find(r => r.name === name);
        if (row) show_tooltip($(this), row_hint(frm, row));
    });

    $wrap.on('mouseleave', '.grid-row', hide_tooltip);
    $(document).on('keydown.nasiya_tip', function (e) {
        if (e.key === 'Escape') hide_tooltip();
    });
}

function inject_tooltip_css() {
    if (document.getElementById('nasiya-tip-css')) return;
    const css = document.createElement('style');
    css.id = 'nasiya-tip-css';
    css.textContent =
        '#nasiya-tip{position:fixed;z-index:1060;max-width:340px;padding:8px 11px;' +
        'border-radius:5px;font-size:12.5px;line-height:1.45;pointer-events:none;' +
        'background:var(--gray-900,#1f272e);color:var(--gray-100,#f4f5f6);' +
        'box-shadow:0 6px 20px rgba(0,0,0,.25);opacity:0;transition:opacity .12s ease}' +
        '#nasiya-tip.on{opacity:1}' +
        '#nasiya-tip .tip-extra{display:block;margin-top:3px;color:var(--orange-300,#eab456)}';
    document.head.appendChild(css);
}

function tip_el() {
    let el = document.getElementById('nasiya-tip');
    if (!el) {
        el = document.createElement('div');
        el.id = 'nasiya-tip';
        el.setAttribute('role', 'tooltip');
        document.body.appendChild(el);
    }
    return el;
}

function show_tooltip($row, hint) {
    const el = tip_el();
    el.textContent = '';

    const main = document.createElement('span');
    main.textContent = hint.main;
    el.appendChild(main);

    if (hint.extra) {
        const extra = document.createElement('span');
        extra.className = 'tip-extra';
        extra.textContent = hint.extra;
        el.appendChild(extra);
    }

    el.classList.add('on');

    const r = $row[0].getBoundingClientRect();
    const t = el.getBoundingClientRect();
    let left = r.left + 20;
    let top = r.bottom + 6;

    if (left + t.width > window.innerWidth - 12) {
        left = Math.max(12, window.innerWidth - t.width - 12);
    }
    if (top + t.height > window.innerHeight - 12) {
        top = r.top - t.height - 6;
    }
    el.style.left = left + 'px';
    el.style.top = Math.max(12, top) + 'px';
}

function hide_tooltip() {
    const el = document.getElementById('nasiya-tip');
    if (el) el.classList.remove('on');
}
