from flask import Flask, request
import requests
import json
import os
from dataclasses import dataclass
from fast_bitrix24 import Bitrix


sms_api_key = os.environ['SMS_CODE']
crm_phone = os.environ['CRM_PHONE']
BITRIX_CODE = os.environ['BITRIX_CODE']
BITRIX_URL = r'https://b24-mgttck.bitrix24.ru/rest/1/' + BITRIX_CODE
endpoint = Bitrix(BITRIX_URL)
app = Flask(__name__)


FIELD_NAME = 'UF_CRM_1742576602338'
FIELD_NAME2 = 'UF_CRM_1742577073481'
FIVE_DAYS_STRING = '''К оплате {} руб. При оплате сегодня +10% бонусов.
Бонусный счет {} руб.
Пришлите в ответ сумму списания бонусов или не отвечайте для их накопления.
Оплатите счёт до {}, получайте бонусы за раннюю оплату! 1 день = 2% бонусов!
'''
OTHER_DAYS_STRING = '''К оплате {} руб. Успейте оплатить, пока не сгорели все бонусы!
Бонусный счет {} руб.
Пришлите в ответ сумму списания бонусов или не отвечайте для их накопления.
Оплатите счёт до {}, получайте бонусы за раннюю оплату! 1 день = 2% бонусов!
'''
MISSED_DAYS_STRING = '''К оплате {} руб. Ждём от вас оплату, а пока ваши бонусы тают!
Бонусный счет {} руб.
'''

### Bitrix


def get_contacts(number: str | None = None):
    truncated_contact_items = endpoint.get_all("crm.contact.list")
    result = []
    for tci in truncated_contact_items:
        current_contact = get_contact_by_ID(tci["ID"])
        if number is not None and number not in current_contact['PHONE']: continue
        result.append(current_contact)
    return result


def get_contact_by_ID(ID):
    ans = endpoint.call("crm.contact.get", items={"ID": ID})
    return ans['order0000000000']


def update_bonus(ID, number, bonus_to_pay=False):
    fn = FIELD_NAME if not bonus_to_pay else FIELD_NAME2
    if number < 0: number = 0
    update_data = [{
        "ID": ID,
        "fields":
            {
                fn: number,
            }
    }]
    endpoint.call("crm.contact.update", items=update_data)


def close_deal(ID):
    update_data = [{
        "ID": ID,
        "fields":
            {
                'CLOSED': 'Y',
            }
    }]
    endpoint.call("crm.deal.update", items=update_data)


def get_deals():
    return endpoint.get_all("crm.deal.list", params={'filter': {'CLOSED': 'N'}})


### Exolve
    
    
def send_SMS(recepient: str, send_str: str):
    payload = {'number': crm_phone, 'destination': recepient, 'text': send_str}
    r = requests.post(r'https://api.exolve.ru/messaging/v1/SendSMS', headers={'Authorization': 'Bearer '+sms_api_key}, data=json.dumps(payload))
    print(r.text)
    return r.text, r.status_code

### Functional


def process_deals(opened_deals: list[dict]):
    for d in opened_deals:
        deadline = datetime.strptime(d['CLOSEDATE'].split('T')[0], '%Y-%m-%d')
        cost = float(d['OPPORTUNITY'])*0.1
        remained_time = (deadline-datetime.now()).days
        contact = get_contact_by_ID(d['CONTACT_ID'])
        bonus = float(contact[FIELD_NAME])
        if remained_time == 5:
            bonus += cost
            send_str = FIVE_DAYS_STRING.format((float(d['OPPORTUNITY']), bonus, deadline.strftime('%d.%m.%Y')))
        else:
            bonus -= cost*0.2  # Уменьшаем зачисленные авансом бонусы или штрафуем за просрочку
            send_str = OTHER_DAYS_STRING.format(float(d['OPPORTUNITY']), bonus, deadline.strftime('%d.%m.%Y'))
        if remained_time < 0:
            send_str = MISSED_DAYS_STRING.format(float(d['OPPORTUNITY']), bonus)
        if bonus < 0: bonus = 0
        if remained_time < -5:
            close_deal(d['ID'])
            send_str = 'Очень жаль, но мы вынуждены с Вами расстаться и разорвать контракт.'
        update_bonus(d['CONTACT_ID'], bonus)
        print(send_SMS(contact['PHONE'][0]['VALUE'].strip('+'), send_str))


@app.route('/receive_sms', methods=['POST'])
def receive_sms():
    SMS_data = request.get_json()
    print(SMS_data)
    if SMS_data.get('event_id') == 'DIRECTION_OUTGOING':
        print('SMS not received')
        return 'Not processed', 200
    try:
        bonus_to_pay = float(SMS_data.get('text'))
    except:
        print('Invalid SMS')
        return 'Invalid SMS', 200
    contact = get_contacts(SMS_data.get('sender'))[0]
    bonus_to_pay = min(bonus_to_pay, float(contact[FIELD_NAME]))
    update_bonus(contact['ID'], bonus_to_pay, bonus_to_pay=True)
    return 'OK', 200


@app.route('/get_paid', methods=['POST'])
def receive_data():
    payment_data = request.get_json()
    print(payment_data)
    amount = payment_data['paid']
    deal_ID = payment_data.get('deal_ID', None)
    deal = endpoint.get_all("crm.deal.list", params={'filter': {'ID': deal_ID}})[0]
    user_ID = deal['CONTACT_ID']
    ct = get_contact_by_ID(user_ID)
    bonuses_to_pay = ct[FIELD_NAME2]
    if amount+bonuses_to_pay < deal['OPPORTUNITY']:
        print('SMS not received')
        return 'Not enouth money', 200
    # Close deal
    close_deal(deal_ID)
    # Subtract bonuses
    bonus = float(ct[FIELD_NAME])
    update_bonus(d['CONTACT_ID'], bonus-bonuses_to_pay)
    return 'OK', 200


def main():
    app.run(host='0.0.0.0', port=5000)


if __name__ == '__main__':
    opened_deals = get_deals()
    process_deals(opened_deals)
    main()
