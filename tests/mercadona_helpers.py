from __future__ import annotations

import base64
import json

def jwt(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return f'x.{encoded}.y'

def write_state(path, *, token: str, refresh: str='refresh') -> None:
    path.write_text(json.dumps({'cookies': [{'name': 'session', 'value': 'cookie', 'domain': '.mercadona.es'}], 'origins': [{'origin': 'https://tienda.mercadona.es', 'localStorage': [{'name': 'MO-user', 'value': json.dumps({'token': token, 'refreshToken': refresh, 'uuid': 'customer-1'})}]}]}), encoding='utf-8')

def cart_payload(version: int=7, total: str='3.00', *, product_id: str='10', name: str='Leche', quantity: float=2, unit_price: str='1.50') -> dict:
    return {'id': 'cart-1', 'version': version, 'products_count': 1, 'summary': {'total': total}, 'lines': [{'quantity': quantity, 'sources': [], 'product': {'id': product_id, 'display_name': name, 'price_instructions': {'unit_price': unit_price}}}]}
