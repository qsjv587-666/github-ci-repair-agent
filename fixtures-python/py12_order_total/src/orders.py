def grand_total(orders):
    return sum(order["subtotal"] for order in orders)
