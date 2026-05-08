# INFORMATION IMPORTANTE : L'AGENT NE DEMANDERA PAS LE NUMERO DE l'APPELANT
# EN EFFET, IL (l'AGENT PEUT DIRECTEMENT SAVOI QUI APPEL)
import asyncio
import os
import datetime
import logging
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.beta.realtime.session import TurnDetection
from twilio.rest import Client
from livekit import agents, rtc, api
from livekit.agents import (
    TurnHandlingOptions,
    RunContext,
    UserStateChangedEvent,
    mcp,
    AgentServer,
    AgentSession,
    Agent,
    JobContext,
    room_io,
    RunContext,
    function_tool,
    ChatContext,
    ChatMessage,
    ConversationItemAddedEvent,
    get_job_context)
from livekit.plugins import openai, noise_cancellation, gladia, ai_coustics,xai
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins.openai import realtime
from system_prompt import SYSTEM_PROMPT, GREETINGS, SUMMARY
from google import genai
from google.genai import types
from pydantic import BaseModel
from typing import Optional
import json
from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip
from mem0 import AsyncMemoryClient
from livekit.agents.beta.tools import EndCallTool

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("garage-agent")

class ClientInfo(BaseModel):
    prenom: str
    nom: Optional[str] = None
    marque_modele_annee: str
    problème: str
    urgence: str
    date_souhaitee_rdv: Optional[str] = None
    numero_suivi: Optional[str] = None


alex_memory = AsyncMemoryClient(api_key="m0-mdKpSdc485RP7ukLF6C2UZbpet8SKR04b8ijVqoe")

async def hangup_call():
    ctx = get_job_context()
    if ctx is None:
        
        return
    
    await ctx.api.room.delete_room(
        api.DeleteRoomRequest(
            room=ctx.room.name,
        )
    )

def call_summary(transcription_file: str = "transcription.txt", phone_number: str = "Non spécifié") -> str:
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    with open(transcription_file, "r", encoding="utf-8") as file:
        data = file.read()
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=f"{SUMMARY}\n\nTranscription:\n{data}",
        config=types.GenerateContentConfig(
            system_instruction=SUMMARY,
            response_mime_type="application/json",
            response_schema=ClientInfo.model_json_schema()
        )
    )
    client_info = ClientInfo.model_validate_json(response.text)
    formatted_message = (
        f"Rapport de l'appel :\n\n"
        f"Résumé de la demande du client\n"
        f"Nom: {client_info.prenom} {client_info.nom or ''}\n"
        f"Numéro téléphone: {phone_number}\n"
        f"Information Véhicule Global: {client_info.marque_modele_annee}\n"
        f"Problème: {client_info.problème}\n"
        f"Urgence: {client_info.urgence}\n"
        f"Date et Heure RDV: {client_info.date_souhaitee_rdv or 'Non spécifiées'}"
    )
    return formatted_message


zapier_token = os.environ.get("ZAPIER_TOKEN")
zapier_url = f"https://mcp.zapier.com/api/v1/connect?token={zapier_token}"

zapier_mcp = mcp.MCPServerHTTP(
    url=zapier_url,
    transport_type="streamable_http",
    timeout = 120.0,
    client_session_timeout_seconds=120.0)


calendar_tool = mcp.MCPToolset(id="zapier",mcp_server=zapier_mcp)


def load_tarifs():
    tarifs_path = os.path.join(os.path.dirname(__file__), "tarifs.json")
    with open(tarifs_path, "r", encoding="utf-8") as f:
        return json.load(f)

# @function_tool
# async def end_call(self, ctx: RunContext):
#         """Utilises ce tool lorsque le problème de l'utilisateur a été réglé"""
#         await ctx.wait_for_playout() 

#         await hangup_call()

@function_tool
def get_tarif_service(context: RunContext,categorie: str, service_nom: str = None) -> str:
    """
    Récupère les tarifs des services du garage.
    
    Args:
        categorie: Catégorie du service (ex: "vidange", "suspension", "freins", "pneus", "services_protection")
                   Ou "tous" pour avoir tous les tarifs
                   Ou "info" pour avoir les informations générales du garage
        service_nom: (Optionnel) Nom spécifique du service dans la catégorie
    
    Returns:
        Les informations de tarification en français
    """
    context.disallow_interruptions()
    
    try:
        tarifs = load_tarifs()
        
        if categorie == "info":
            return (
                f"Garage : {tarifs['garage_info']['nom']}\n"
                f"Adresse : {tarifs['garage_info']['adresse']}\n"
                f"Téléphone : {tarifs['garage_info']['telephone']}\n\n"
                f"Main d'œuvre : {tarifs['main_doeuvre']['tarif_horaire']}\n"
                f"Garantie pièce : {tarifs['main_doeuvre']['garantie_piece']}\n"
                f"Si pièce externe : {tarifs['main_doeuvre']['piece_externe']}"
            )
        
        if categorie == "tous":
            result = []
            for cat, services in tarifs["services"].items():
                cat_name = cat.replace("_", " ").title()
                result.append(f"\n--- {cat_name} ---")
                for service in services:
                    result.append(
                        f"- {service['nom']} : {service['prix']} ({service['duree']})"
                    )
            return "\n".join(result)
        
        if categorie not in tarifs["services"]:
            return (
                f"Catégorie '{categorie}' non trouvée. "
                f"Catégories disponibles : vidange, suspension, freins, pneus, services_protection"
            )
        
        services = tarifs["services"][categorie]
        
        if service_nom:
            for service in services:
                if service_nom.lower() in service["nom"].lower():
                    return (
                        f"Service : {service['nom']}\n"
                        f"Prix : {service['prix']}\n"
                        f"Durée : {service['duree']}"
                    )
            return f"Service '{service_nom}' non trouvé dans la catégorie '{categorie}'."
        
        result = [f"--- {categorie.replace('_', ' ').title()} ---"]
        for service in services:
            result.append(
                f"- {service['nom']} : {service['prix']} ({service['duree']})"
            )
        return "\n".join(result)
        
    except Exception as e:
        return f"Erreur lors de la récupération des tarifs : {str(e)}"


class Alex(Agent):
    def __init__(self, phone_number: str):
        
        self.phone_number = phone_number
        current_date = datetime.datetime.now().strftime("%A %d %B %Y")
        dynamic_instructions = (
            f"{SYSTEM_PROMPT}\n\n"
            f"### CONTEXTE TEMPOREL (TRÈS IMPORTANT)\n"
            f"- DATE D'AUJOURD'HUI : {current_date}\n"
            f"- Tu dois TOUJOURS utiliser cette date comme point de repère pour le calendrier.\n"
            f"- N'invente JAMAIS d'année (nous sommes en {datetime.datetime.now().year}).\n"
            f"- Si le client dit 'jeudi', il s'agit du PROCHAIN jeudi à partir d'aujourd'hui, ne choisis JAMAIS une date dans le passé (comme 2024).\n\n"
            f"### CONFIGURATION TECHNIQUE (CRITIQUE)\n"
            f"- ID CALENDRIER DU GARAGE : 'garageroadr@gmail.com'\n"
            f"- CONSIGNE CALENDRIER : Pour CHAQUE opération (recherche ou création), utilise TOUJOURS 'garageroadr@gmail.com' comme 'calendarid'. Ne demande JAMAIS l'email du client.\n"
            f"- FORMAT TITRE : Le champ 'summary' de l'événement doit être : 'NomClient {phone_number}'.\n"
            f"- LANGUE DESCRIPTION : La 'description' de l'événement DOIT être rédigée en français et inclure les détails du véhicule (ex: 'Réparation de la Ferrari 1960').\n"
            f"- VALEURS STRICTES ZAPIER : Lors de la création d'événement, tu DOIS régler 'transparency' sur 'opaque' et 'visibility' sur 'private'.\n"
            f"- OBLIGATION ZAPIER : Tu DOIS fournir l'argument 'instructions' (en anglais) pour chaque appel d'outil Zapier, sinon ça échouera."
        )
        super().__init__(instructions=dynamic_instructions, tools=[calendar_tool, get_tarif_service])

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        await alex_memory.add(new_message.text_content, user_id=self.phone_number)
        memories = await alex_memory.search(new_message.text_content, user_id=self.phone_number)

        if memories:
            memory_text = "\n".join([m['memory'] for m in memories])
            turn_ctx.add_message(
                role="system", 
                content=f"Rappel de tes souvenirs sur ce client : {memory_text}"
            )
            await self.update_chat_ctx(turn_ctx)


server = AgentServer()

@server.rtc_session(agent_name="alex_garage")
async def garage_agent(ctx: agents.JobContext):
    gladia_key = os.environ.get("GLADIA_API_KEY")
    phone_number = "Inconnu"
   
    with open("transcription.txt", "w", encoding="utf-8") as f:
        f.write("")
    session = AgentSession(
        turn_handling=TurnHandlingOptions(
        turn_detection=MultilingualModel(),
        interruption={
            "mode": "adaptive",
        },
    ),
        user_away_timeout=15.0,
        stt=gladia.STT(api_key=gladia_key, languages=["fr", "en", "es"]),
        # llm= xai.realtime.RealtimeModel(
        #     voice = "Ara",
        #      turn_detection=TurnDetection(
        #         type="server_vad",
        #         eagerness="auto",
        #         interrupt_response=True,
        #     ),

        # )
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime-1.5",
            voice="coral",
            turn_detection=TurnDetection(
                type="semantic_vad",
                eagerness="auto",
                interrupt_response=True,
            ),
        ),
    )

    @session.on("user_state_changed")
    def end_call(user_presence : UserStateChangedEvent) : 
        if user_presence.new_state == "away":
            asyncio.create_task(hangup_call())
           
            
    
    @session.on("conversation_item_added")
    def transcription(transcript: ConversationItemAddedEvent):
        if not isinstance(transcript.item, ChatMessage):
            return
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open("transcription.txt", "a", encoding="utf-8") as file:
                if transcript.item.role == "assistant":
                    content = transcript.item.text_content or ""
                    print(f"Alex : {content}")
                    file.write(f"{timestamp} Alex : {content}\n")
                elif transcript.item.role == "user":
                    content = transcript.item.text_content or ""
                    print(f"Appelant : {content}")
                    file.write(f"{timestamp} Appellant : {content}\n")
        except Exception as e:
            logger.error(f"Failed to write to transcription.txt: {e}")

    caller = await ctx.wait_for_participant()
    if caller.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        phone_number = caller.attributes.get("sip.phoneNumber", "unknown")
        logger.info(f"{phone_number} is calling...")
    async def send_summary():
        logger.info("Appel terminé. Génération du résumé...")
        try:
            if os.path.exists("transcription.txt"):
                summary_message = call_summary("transcription.txt", phone_number)
                account_sid = os.environ.get("ACCOUNT_SID")
                auth_token = os.environ.get("AUTH_TOKEN")
                if account_sid and auth_token:
                    twilio_client = Client(account_sid, auth_token)
                    twilio_client.messages.create(
                        body=summary_message,
                        from_=os.environ.get("SENDER_PHONE_NUMBER"),
                        to=os.environ.get("RECEIVER_PHONE_NUMBER")
                    )
                    logger.info("Résumé envoyé avec succès")
                else:
                    logger.error("Variables d'environnement Twilio manquantes")
            else:
                logger.warning("Fichier transcription.txt non trouvé")
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi du résumé: {e}")
    ctx.add_shutdown_callback(send_summary)
    await session.start(
        record=True,
        room=ctx.room,
        agent=Alex(phone_number),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_L
                ),
            ),
        ),
    )

    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.9),
        thinking_sound=[
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.9),
        ],
    )
    await background_audio.start(room=ctx.room, agent_session=session)

    await session.generate_reply(
        instructions=(
            f"Salue TOUJOURS client en disant exactement : '{GREETINGS.strip()}'. "
        )
    )
if __name__ == "__main__":
    agents.cli.run_app(server)