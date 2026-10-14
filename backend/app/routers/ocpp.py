"""Websocket OCPP 1.6J: o carregador se conecta em /ocpp/{carregador_id}."""
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import get_db
from app.integracoes import ocpp16

router = APIRouter(tags=["OCPP"])


@router.websocket("/ocpp/{carregador_id}")
async def conexao_ocpp(websocket: WebSocket, carregador_id: str, db: Session = Depends(get_db)):
    # o OCPP-J exige que o carregador peça o subprotocolo da versão; sem ele, a spec
    # manda completar o handshake sem subprotocolo e fechar em seguida
    if "ocpp1.6" not in websocket.scope.get("subprotocols", []):
        await websocket.accept()
        await websocket.close(code=1002)
        return
    await websocket.accept(subprotocol="ocpp1.6")
    try:
        while True:
            texto = await websocket.receive_text()
            # o domínio é síncrono (SQLAlchemy): roda fora do laço de eventos
            resposta = await run_in_threadpool(ocpp16.processar_texto, db, carregador_id, texto)
            if resposta is not None:
                await websocket.send_text(resposta)
    except WebSocketDisconnect:
        pass
