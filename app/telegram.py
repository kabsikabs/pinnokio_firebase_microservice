import json
import asyncio
from telethon import TelegramClient, events
import os
from datetime import datetime
import random
import string
from ..tools.g_cred import get_secret
from dotenv import load_dotenv
load_dotenv()
class TelegramUserRegistration:
    """Classe simplifiée pour l'enregistrement des utilisateurs Telegram"""
    
    def __init__(self, expected_username: str, expected_code: str, success_callback, error_callback, secret_name: str = "telegram_pinnokio"):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Configuration Telegram
        cfg = json.loads(get_secret(secret_name))
        config_keys = cfg['App configuration']
        api_id = config_keys.get("api_id")
        api_hash = config_keys.get("api_hash")
        bot_token = config_keys.get("bot_api_id")
        session_suffix = os.getenv("ENV", "local")  # Ex: "prod", "dev", "test"
        session_name = f"pinnokio_dashboard_{session_suffix}"

        self.client = TelegramClient(session_name, api_id, api_hash)
        self.bot_token = bot_token
        
        # Paramètres d'enregistrement
        self.expected_username = expected_username.lower().replace("@", "")
        self.expected_code = expected_code
        self.success_callback = success_callback
        self.error_callback = error_callback
        self.is_listening = False
        self.registration_completed = False
        
        # ✅ NOUVEAU : Queue pour communication async
        self.callback_queue = asyncio.Queue()
        self.callback_processor = None
        
        print(f"🔧 TelegramUserRegistration initialisé pour @{self.expected_username} avec code {self.expected_code}")
    
    async def connect(self):
        """Connecte le client Telegram"""
        try:
            await self.client.start(bot_token=self.bot_token)
            print("✅ Client Telegram connecté pour l'enregistrement")
            return True
        except Exception as e:
            print(f"❌ Erreur de connexion Telegram: {e}")
            # ✅ METTRE EN QUEUE AU LIEU D'APPELER DIRECTEMENT
            await self.callback_queue.put({
                'type': 'error',
                'message': "Failed to connect to Telegram. Please try again."
            })
            return False
    
    async def start_listening(self):
        """Démarre l'écoute des messages pour l'enregistrement"""
        if not await self.connect():
            return False
        
        self.is_listening = True
        print(f"👂 Écoute démarrée pour @{self.expected_username}")
        
        # ✅ DÉMARRER LE PROCESSEUR DE CALLBACKS
        self.callback_processor = asyncio.create_task(self._process_callbacks())
        
        @self.client.on(events.NewMessage)
        async def message_handler(event):
            if not self.is_listening or self.registration_completed:
                return
            
            try:
                # ✅ LOGS DÉTAILLÉS POUR DEBUGGING - AVANT TOUT FILTRE
                print(f"🔍 NOUVEAU MESSAGE CAPTURÉ:")
                
                print(f"  🆔 Chat ID: {event.chat_id}")
                print(f"  📝 Texte brut: '{event.message.message}'")
                
                # Récupérer les informations de l'expéditeur
                sender = await event.get_sender()
                if not sender or not hasattr(sender, 'username'):
                    return
                
                sender_username = sender.username.lower() if sender.username else ""
                message_text = event.message.message.strip()
                chat_id = event.chat_id
                
                print(f"📨 Message reçu de @{sender_username}: '{message_text}'")
                
                # Vérifier si c'est le bon utilisateur et le bon code
                if sender_username == self.expected_username and message_text == self.expected_code:
                    print(f"✅ Code validé pour @{sender_username}")
                    
                    # Marquer comme complété pour éviter les doublons
                    self.registration_completed = True
                    
                    # Envoyer message de confirmation à l'utilisateur
                    success_message = (
                        f"🎉 Registration successful!\n\n"
                        f"Welcome to Pinnokio! Your account (@{self.expected_username}) "
                        f"has been successfully registered and linked to this chat.\n\n"
                        f"You can now receive notifications and interact with your Pinnokio agents through this channel."
                    )
                    
                    try:
                        await self.client.send_message(chat_id, success_message)
                    except Exception as e:
                        print(f"⚠️ Impossible d'envoyer le message de confirmation: {e}")
                    
                    # ✅ METTRE EN QUEUE AU LIEU D'APPELER DIRECTEMENT
                    await self.callback_queue.put({
                        'type': 'success',
                        'chat_id': chat_id,
                        'username': sender_username
                    })
                    
                    # Arrêter l'écoute
                    await self.stop_listening()
                
            except Exception as e:
                print(f"❌ Erreur lors du traitement du message: {e}")
        
        return True
    
    async def _process_callbacks(self):
        """✅ NOUVEAU : Processeur de callbacks qui s'exécute dans un contexte séparé"""
        try:
            while self.is_listening or not self.callback_queue.empty():
                try:
                    # Attendre un callback avec timeout
                    callback_data = await asyncio.wait_for(
                        self.callback_queue.get(), 
                        timeout=1.0
                    )
                    
                    if callback_data['type'] == 'success':
                        # ✅ PROGRAMMER L'EXÉCUTION DU CALLBACK DANS LE BON CONTEXTE
                        asyncio.create_task(self._execute_success_callback(
                            callback_data['chat_id'], 
                            callback_data['username']
                        ))
                    elif callback_data['type'] == 'error':
                        # ✅ PROGRAMMER L'EXÉCUTION DU CALLBACK D'ERREUR
                        asyncio.create_task(self._execute_error_callback(
                            callback_data['message']
                        ))
                    
                except asyncio.TimeoutError:
                    # Timeout normal, continuer la boucle
                    continue
                except Exception as e:
                    print(f"❌ Erreur dans le processeur de callbacks: {e}")
                    
        except Exception as e:
            print(f"❌ Erreur fatale dans le processeur de callbacks: {e}")
    
    async def _execute_success_callback(self, chat_id: int, username: str):
        """✅ NOUVEAU : Exécute le callback de succès dans un contexte séparé"""
        try:
            await self.success_callback(chat_id, username)
        except Exception as e:
            print(f"❌ Erreur lors de l'exécution du callback de succès: {e}")
    
    async def _execute_error_callback(self, error_message: str):
        """✅ NOUVEAU : Exécute le callback d'erreur dans un contexte séparé"""
        try:
            await self.error_callback(error_message)
        except Exception as e:
            print(f"❌ Erreur lors de l'exécution du callback d'erreur: {e}")
    
    async def stop_listening(self):
        """Arrête l'écoute et déconnecte le client"""
        try:
            self.is_listening = False
            
            # ✅ ARRÊTER LE PROCESSEUR DE CALLBACKS
            if self.callback_processor and not self.callback_processor.done():
                self.callback_processor.cancel()
                try:
                    await self.callback_processor
                except asyncio.CancelledError:
                    pass
            
            if self.client.is_connected():
                await self.client.disconnect()
            print("🔌 Client Telegram déconnecté")
        except Exception as e:
            print(f"⚠️ Erreur lors de la déconnexion: {e}")