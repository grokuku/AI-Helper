#!/usr/bin/env python3
"""
Script de diagnostic pour tester l'API DeepSeek directement.
Permet d'identifier l'erreur 400 sans passer par le backend Flask.

Usage:
    cd backend
    python3 test_deepseek.py

    # Ou avec des paramètres personnalisés:
    python3 test_deepseek.py --api-key sk-xxx --model deepseek-chat --base-url https://api.deepseek.com
"""

import argparse
import json
import sys
import os

try:
    import requests
except ImportError:
    print("ERREUR: Le module 'requests' n'est pas installé.")
    print("Installe-le avec: pip install requests")
    sys.exit(1)


def test_deepseek(api_key, model, base_url):
    """Teste l'API DeepSeek avec différents payloads pour isoler l'erreur."""

    # Normaliser l'URL
    base_url = base_url.rstrip('/')
    if not base_url.endswith('/chat/completions'):
        url = f"{base_url}/chat/completions"
    else:
        url = base_url

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }

    print(f"{'='*60}")
    print(f"Diagnostic DeepSeek")
    print(f"{'='*60}")
    print(f"URL:     {url}")
    print(f"Model:   {model}")
    print(f"API Key: {api_key[:8]}...{api_key[-4:] if len(api_key) > 12 else '***'}")
    print()

    # ── Test 1: Payload minimal (juste model + messages) ──────────────
    print("── Test 1: Payload minimal (model + messages) ──")
    payload_min = {
        'model': model,
        'messages': [
            {'role': 'user', 'content': 'Say hello in one word.'}
        ],
    }
    print(f"Payload: {json.dumps(payload_min, indent=2)}")
    try:
        r = requests.post(url, headers=headers, json=payload_min, timeout=30)
        print(f"Status: {r.status_code}")
        print(f"Body:   {r.text[:500]}")
        if r.ok:
            print("✓ Test 1 RÉUSSI — l'API fonctionne avec un payload minimal.")
        else:
            print("✗ Test 1 ÉCHEC — voir le body ci-dessus pour l'erreur exacte.")
    except Exception as e:
        print(f"EXCEPTION: {e}")
    print()

    # ── Test 2: Avec temperature ──────────────────────────────────────
    print("── Test 2: Avec temperature=0.3 ──")
    payload_temp = {
        'model': model,
        'messages': [
            {'role': 'user', 'content': 'Say hello in one word.'}
        ],
        'temperature': 0.3,
    }
    try:
        r = requests.post(url, headers=headers, json=payload_temp, timeout=30)
        print(f"Status: {r.status_code}")
        print(f"Body:   {r.text[:500]}")
        if r.ok:
            print("✓ Test 2 RÉUSSI — temperature est accepté.")
        else:
            print("✗ Test 2 ÉCHEC — temperature pourrait être le problème.")
    except Exception as e:
        print(f"EXCEPTION: {e}")
    print()

    # ── Test 3: Avec frequency_penalty ────────────────────────────────
    print("── Test 3: Avec frequency_penalty=0.5 ──")
    payload_freq = {
        'model': model,
        'messages': [
            {'role': 'user', 'content': 'Say hello in one word.'}
        ],
        'temperature': 0.3,
        'frequency_penalty': 0.5,
    }
    try:
        r = requests.post(url, headers=headers, json=payload_freq, timeout=30)
        print(f"Status: {r.status_code}")
        print(f"Body:   {r.text[:500]}")
        if r.ok:
            print("✓ Test 3 RÉUSSI — frequency_penalty est accepté.")
        else:
            print("✗ Test 3 ÉCHEC — frequency_penalty EST LE PROBLÈME!")
    except Exception as e:
        print(f"EXCEPTION: {e}")
    print()

    # ── Test 4: Vérifier les modèles disponibles ──────────────────────
    print("── Test 4: Lister les modèles disponibles ──")
    models_url = base_url.rstrip('/chat/completions') + '/models'
    if '/chat/completions' in models_url:
        models_url = models_url.replace('/chat/completions', '/models')
    try:
        r = requests.get(models_url, headers=headers, timeout=10)
        print(f"GET {models_url}")
        print(f"Status: {r.status_code}")
        if r.ok:
            data = r.json()
            models = data.get('data', data.get('models', []))
            model_ids = [m.get('id', m.get('name', '?')) for m in models]
            print(f"Modèles disponibles: {model_ids}")
            if model not in model_ids:
                print(f"⚠️  ATTENTION: Le modèle '{model}' n'est PAS dans la liste!")
                print(f"   Modèles valides: {model_ids}")
                print(f"   Corrige le modèle dans ton preset AI-Helper.")
            else:
                print(f"✓ Le modèle '{model}' est valide.")
        else:
            print(f"Body: {r.text[:500]}")
    except Exception as e:
        print(f"EXCEPTION: {e}")
    print()

    # ── Test 5: URL alternative avec /v1/ ─────────────────────────────
    if '/v1/' not in url:
        alt_url = url.replace('/chat/completions', '/v1/chat/completions')
        print(f"── Test 5: URL alternative avec /v1/ ──")
        print(f"URL: {alt_url}")
        try:
            r = requests.post(alt_url, headers=headers, json=payload_min, timeout=30)
            print(f"Status: {r.status_code}")
            print(f"Body:   {r.text[:500]}")
            if r.ok:
                print("✓ Test 5 RÉUSSI — l'URL avec /v1/ fonctionne aussi.")
            else:
                print("✗ Test 5 ÉCHEC — l'URL avec /v1/ ne fonctionne pas non plus.")
        except Exception as e:
            print(f"EXCEPTION: {e}")
        print()

    print(f"{'='*60}")
    print("Diagnostic terminé. Vérifie les résultats ci-dessus.")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description='Diagnostic DeepSeek API')
    parser.add_argument('--api-key', default=os.environ.get('DEEPSEEK_API_KEY', ''),
                        help='API key DeepSeek (ou variable DEEPSEEK_API_KEY)')
    parser.add_argument('--model', default='deepseek-chat',
                        help='Nom du modèle (défaut: deepseek-chat)')
    parser.add_argument('--base-url', default='https://api.deepseek.com',
                        help='URL de base (défaut: https://api.deepseek.com)')
    args = parser.parse_args()

    if not args.api_key:
        print("ERREUR: API key requise.")
        print("Usage: python3 test_deepseek.py --api-key sk-xxx")
        print("   ou: DEEPSEEK_API_KEY=sk-xxx python3 test_deepseek.py")
        sys.exit(1)

    test_deepseek(args.api_key, args.model, args.base_url)


if __name__ == '__main__':
    main()