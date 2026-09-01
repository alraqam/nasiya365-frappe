/**
 * Installment Calculator
 * Provides a quick calculator accessible from navbar for computing monthly installment payments
 */

frappe.provide('nasiya365');

nasiya365.InstallmentCalculator = class {
    constructor() {
        this.dialog = null;
        this.setup_navbar_icon();
    }

    setup_navbar_icon() {
        // Add calculator icon to navbar
        $(document).on('toolbar_setup', () => {
            const navbar = $('.navbar-right');
            if (navbar.length && !$('#installment-calculator-icon').length) {
                const icon = $(`
                    <li id="installment-calculator-icon">
                        <a href="#" title="${__('Калькулятор рассрочки')}">
                            <i class="fa fa-calculator"></i>
                        </a>
                    </li>
                `);

                icon.on('click', (e) => {
                    e.preventDefault();
                    this.show_calculator();
                });

                navbar.prepend(icon);
            }
        });
    }

    show_calculator() {
        if (!this.dialog) {
            this.dialog = new frappe.ui.Dialog({
                title: __('Калькулятор рассрочки'),
                fields: [
                    {
                        fieldname: 'product_price',
                        fieldtype: 'Currency',
                        label: __('Цена товара'),
                        reqd: 1,
                        default: 0,
                        onchange: () => this.calculate()
                    },
                    {
                        fieldtype: 'Column Break'
                    },
                    {
                        fieldname: 'down_payment_percent',
                        fieldtype: 'Percent',
                        label: __('Первоначальный взнос %'),
                        default: 20,
                        onchange: () => this.calculate()
                    },
                    {
                        fieldtype: 'Section Break'
                    },
                    {
                        fieldname: 'installment_months',
                        fieldtype: 'Select',
                        label: __('Срок рассрочки (месяцы)'),
                        options: ['3', '6', '9', '12', '18', '24'],
                        default: '12',
                        onchange: () => this.calculate()
                    },
                    {
                        fieldtype: 'Column Break'
                    },
                    {
                        fieldname: 'interest_rate',
                        fieldtype: 'Percent',
                        // Ставка МЕСЯЧНАЯ — как в договоре. Раньше поле называлось
                        // годовым и делилось на 12, а договор трактовал то же число
                        // как месячное: на вводе «2» калькулятор показывал 10.87
                        // процентов там, где договор печатал 240.00.
                        label: __('Ставка % (мес.)'),
                        default: 2,
                        description: __('Та же ставка, что в договоре — за месяц'),
                        onchange: () => this.calculate()
                    },
                    {
                        fieldtype: 'Section Break',
                        label: __('Результаты')
                    },
                    {
                        fieldname: 'down_payment_amount',
                        fieldtype: 'Currency',
                        label: __('Сумма первоначального взноса'),
                        read_only: 1
                    },
                    {
                        fieldtype: 'Column Break'
                    },
                    {
                        fieldname: 'financed_amount',
                        fieldtype: 'Currency',
                        label: __('Сумма финансирования'),
                        read_only: 1
                    },
                    {
                        fieldtype: 'Section Break'
                    },
                    {
                        fieldname: 'monthly_payment',
                        fieldtype: 'Currency',
                        label: __('Ежемесячный платеж'),
                        read_only: 1
                    },
                    {
                        fieldtype: 'Column Break'
                    },
                    {
                        fieldname: 'total_interest',
                        fieldtype: 'Currency',
                        label: __('Общая переплата'),
                        read_only: 1
                    },
                    {
                        fieldtype: 'Section Break'
                    },
                    {
                        fieldname: 'total_payment',
                        fieldtype: 'Currency',
                        label: __('Общая сумма выплат'),
                        read_only: 1
                    }
                ],
                primary_action_label: __('Закрыть'),
                primary_action: () => {
                    this.dialog.hide();
                }
            });
        }

        this.dialog.show();
        this.calculate();
    }

    calculate() {
        if (!this.dialog) return;

        const values = this.dialog.get_values();
        if (!values || !values.product_price) return;

        const price = parseFloat(values.product_price) || 0;
        const down_percent = parseFloat(values.down_payment_percent) || 0;
        const months = parseInt(values.installment_months) || 12;
        const rate = parseFloat(values.interest_rate) || 0;
        const down_payment = price * (down_percent / 100);

        // Считает СЕРВЕР — той же функцией, что и договор. Своей формулы здесь
        // больше нет: пока их было две, калькулятор применял аннуитет по годовой
        // ставке, а договор — flat по месячной, и продавец называл покупателю
        // цифру, которой в договоре не появлялось.
        frappe.call({
            method: 'nasiya365.nasiya365.doctype.installment_plan.installment_plan.calculate_installment_preview',
            args: {
                principal: price,
                down_payment: down_payment,
                interest_rate: rate,
                num_installments: months,
                frequency: 'Ежемесячно (Monthly)',
                start_date: frappe.datetime.get_today(),
            },
            callback: (r) => {
                if (!r.message) return;
                this._render(down_payment, r.message);
            },
        });
    }

    _render(down_payment, preview) {
        const total_interest = preview.total_interest || 0;
        const monthly_payment = preview.installment_amount || 0;
        const total_payment = preview.total_amount || 0;

        this.dialog.set_value('down_payment_amount', down_payment.toFixed(2));
        this.dialog.set_value('financed_amount', (preview.financed_amount || 0).toFixed(2));
        this.dialog.set_value('monthly_payment', monthly_payment.toFixed(2));
        this.dialog.set_value('total_interest', total_interest.toFixed(2));
        this.dialog.set_value('total_payment', total_payment.toFixed(2));
    }
};

// Initialize calculator when page loads
$(document).ready(() => {
    nasiya365.calculator = new nasiya365.InstallmentCalculator();
});
