import json
import asyncio
from time import sleep
from gpiozero import Servo, AngularServo
from datetime import datetime, timedelta
import backoff
import requests
from requests import HTTPError

PROFILE_ID = None
POLLING_TIMEOUT = 1  # 2 seconds
USER_GET_FOOD_TIMEOUT = 5  # seconds


RISTRETTO = 'Ristretto'
ISPIRAZIONE = 'Ispirazione'
VOLLUTO = 'Volluto'

ORDERS_URL = None
ORDERS_BUMP_URL = None
LOGIN_URL = None


SERVO_PINS = {
    RISTRETTO: 17,
    ISPIRAZIONE: 27,
    VOLLUTO: 22,
}

CUP_SERVO_PIN = 4


def drop_capsule(capsule_type: str, count: int):
    servo = Servo(SERVO_PINS[capsule_type])
    for _ in range(count):
        servo.min()
        sleep(0.3)
        servo.max()
        sleep(0.5)

    servo.close()



def get_angular_servo(pin_number: int):
    return AngularServo(
        pin_number,
        min_angle=-90,
        max_angle=90,
        min_pulse_width=0.5 / 1000,
        max_pulse_width=2.4 / 1000,
        frame_width=20 / 1000
    )


def drop_cups(count: int):
    servo = get_angular_servo(pin_number=CUP_SERVO_PIN)
    for _ in range(count):
        servo.min()  # inside
        sleep(0.3)
        for _ in range(5):
            servo.angle = -45
            sleep(0.1)
            servo.min()
            sleep(0.1)
        servo.max()
        sleep(0.3)

    servo.close()


class CoffeeMachine:
    TOKEN_TIMEOUT_MINUTES = 15

    @staticmethod
    def _get_token_from_server():
        print('Login to kds...')
        url = LOGIN_URL
        res = requests.post(url, json={
            'device': 'web',
            'os': 'os',
            'imei': 'imei',
            'protocol': '2.1',
            'language': 'en-US',
            'version': '',
            'pdr_app_type': 'kds',
            'auth': {
                'username': None,
                'password': None
            }
        })
        try:
            token = json.loads(res.text)["auth"]["access"]
            print(f'token is: {token}')
            return token

        except Exception as err:
            print(err)

    def get_token(self):
        if not self._token_timeout_time or datetime.now() > (self._token_timeout_time + timedelta(minutes=self.TOKEN_TIMEOUT_MINUTES)):
            print('Gets Token')
            self._token = self._get_token_from_server()
            self._token_timeout_time = datetime.now()
        return self._token

    def __init__(self):
        self._token = None
        self._token_timeout_time = None
        self.get_token()
        self.orders_black_list = []

    async def get_orders(self):
        print('Getting Orders', end='')
        orders = []
        while not orders:
            print('.', end='')
            # TODO: maybe move to async
            res = requests.get(ORDERS_URL,
                               headers={"Authorization": f"Bearer {self.get_token()}"})
            if not res:
                print('Get Orders Failed!')
                return []

            try:
                orders = json.loads(res.text)['orders']

            except Exception as err:
                print(err)

            await asyncio.sleep(POLLING_TIMEOUT)

        order_ids = ', '.join([str(order['order_id']) for order in orders])
        print('/')
        print(f'Got orders: [{order_ids}]')
        return orders

    @backoff.on_exception(backoff.constant, HTTPError, max_tries=3, interval=1)
    def bump_order(self, order):
        kds_order_id = order['_id']
        res = requests.post(ORDERS_BUMP_URL,
                            json={'kds_order_id': kds_order_id},
                            headers={"Authorization": f"Bearer {self.get_token()}"})
        res.raise_for_status()
        order_id = order['order_id']
        self.orders_black_list.append(order_id)
        print(f'Bumping {order_id}')
        print(res.text)
        return res

    @staticmethod
    async def drop_capsules_from_order(order):
        promises = []
        for kds_item in order['kds_items']:
            name = kds_item['name']
            quantity = kds_item['quantity']
            if name in SERVO_PINS.keys():
                print(f'dropping {quantity} {name}')
                promises.append(asyncio.to_thread(drop_capsule, name, quantity))

        cups_count = len(promises)
        promises.append(asyncio.to_thread(drop_cups, cups_count))
        await asyncio.wait(promises)

    async def run(self):
        while True:
            orders = await self.get_orders()
            for order in orders:
                order_id = order['order_id']
                print(f'Reviewing order: {order_id}')
                if order_id in self.orders_black_list:
                    continue

                await self.drop_capsules_from_order(order)
                self.bump_order(order)

                await asyncio.sleep(USER_GET_FOOD_TIMEOUT)

            await asyncio.sleep(POLLING_TIMEOUT)


if __name__ == '__main__':
    asyncio.run(CoffeeMachine().run())
