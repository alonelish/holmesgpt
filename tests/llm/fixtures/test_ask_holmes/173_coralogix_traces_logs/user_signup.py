import requests
import time
import random

PAYMENT_SERVICE_URL = "http://payment:8080"


def generate_signup_request():
    """Generate a user signup/payment request with random data."""
    user_id = f"user-{random.randint(1000, 9999)}"
    amount = round(random.uniform(10.0, 500.0), 2)

    payload = {"user_id": user_id, "amount": amount}

    try:
        response = requests.post(
            f"{PAYMENT_SERVICE_URL}/payment",
            json=payload,
            timeout=10,
        )
        if response.status_code == 200:
            print(f"✓ Signup/payment successful: {payload}")
        else:
            print(f"✗ Signup/payment failed: {response.status_code} - {payload}")
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Request error: {exc}")


if __name__ == "__main__":
    print("Starting user-signup traffic generator for payment service...")
    print(f"Target: {PAYMENT_SERVICE_URL}")

    # Generate traffic for 2 minutes
    end_time = time.time() + 120
    request_count = 0

    while time.time() < end_time:
        generate_signup_request()
        request_count += 1
        # Random delay between requests (0.5 to 2 seconds)
        time.sleep(random.uniform(0.5, 2.0))

    print(f"\nTraffic generation complete. Sent {request_count} requests.")

