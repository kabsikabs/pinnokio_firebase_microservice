Extraction de context router context

Créer un outil pour la recherche des informations sur le département Router, et ensuite en dessous un array avec les département ou on souhaite le context actuelle
selon les département et les services
Il existe 3 départements princiapals et des services aux département.
Les déparemtnes sont 'ACCOUNTING' 'HR' 'LEGAL and Administration'
donc l'outil pour la recherhce des context par département qui permettent au router de savoir comment archiver et classer les documents sont basé sur ces context 
'banks_cash' pour ce qui concerne les banques et les liquidité
contrats, expenses (note de frais), 'financial_statement', 'hr' ,'invoices', 'letters', 'taxes', 
la méthode pour acceder a ces dernier via la méthode dans firebase_provider appelé 'load_automated_router_context' et mettre le mandate path comme argument (voir les métadonnée recolté apres initaliation session )
l'output est un tuple, defintion_prompt, et instruct_prompt--- prendre definition prompt 

donner en text comme output en fonction du choix de l'agent.

créer la méthode pour récupere le context dans mandate_path/accounting_context/ prendre le dictionnaire clé data et dedans prendre le champs 'accounting_context_0'
donner l'output.
et le context général 

et le context génréal , sous le chemin mandate_path/general_context prendre le champs du document appelé 'context_company_profile_report' --- Créer la méthode pour extraire ceci


Donc sur la base de ceci , créer les outils et les format json pour extraire ces informations en fonction de la question que l'agent recoit

ensuite créer l'outil update context créer un outil qui va définir le service en argument, cette argument doit etre un enum des services disponible, 
en fonction du choix du service appelé les méthode cité ci dessus pour extraire le context.
le deuxieme argument va etre les kwars autorisé par l'outil text_updater qui permet de faire plusieurs modifications.
ensuite inserer un flux d'approbation via le chat , avec la proposition de modification apporté 
l'utilisateur approve , sauvé dans firebase et mise à jour du champs 'last_refresh' avec ce format de date '"2025-10-05T14:36:32.053501"' pour general_context
mise à jour du champs last_refresh
"2025-10-05T14:38:55.981286"^^ pour router_context
et egalemetn 'last_refresh
"2025-10-05T14:37:01.060208"' pour accounting_context mais attention ce champs est dans le dictionnaire data

***************
Donc cet agent SPT Context_manager sera un outil pour l'agent principale mais il doit avoir exactemnt la meme structure sur boucle agentic, un system_prmpt, un premier message provenant de l'agent princiaple ensuite une boucle de gestion des outils contorole par une fenetre de context de 30K , et un syteme de resumé pour en faire le premier mesage dans le cas dépassement.
deux méthode de sorite, soit par le task_terminate qui est un util qui permet de la cloturer la tache demande soit par le text_output qui est renvoyé à l'agent principale , exactement de la meme manière que nous gerons au niveau de l'agent princiaple, soit il termine la tache si outil demande soit il repond textuellement ( donc appliquer le meme principe au niveau de l'agent outil) ceci permettra de pouvoir assurer une conversation entre agents si besoin de plus précision , il faudra jsute mettre du text en préfix a ce text COMMENTAIRE DE L'AGENT (Vous pouvez lui répodnre direcment en tuilisant l'outil et mettre votre response )ou sinon vous avez besoin de recolter l'information aupres de l'utilisateur , questionner directement l'utilisateur et reporter sa réponse directement à l'agent. 
l'agent outil etant son propre BaseAiAgent dispose de son propre chat_history donc, effacer le chat_history immédiatement a chaque boucle de task_terminer ou si sorti par text mettre un ttl de 30 minutes sur le chat_history ceci va permettre d'avoir les chat programmé a s'effacer sans intervention humaine.

donc il faut créer le prompt system de cet agent, et intégrer une explication tres high level au niveau de l'agent principale le role de cet outil et son outil aura comme argument seulement question, et sur cette base de question l'agent devra s'avoir quoi faire par exemple.
explique moi stp le role du département Taxes que dois je clalsser la bas----il recoit le context et rponde a la qeustion en fontion , utilsier son outil de terminate pour terminer la tache et remet a l'âgent principale la réponse.
Ensuite l'agent principale , demande de d'integrer au context les instructions qui concernet ce département sur quoi faire concernant le traitement et la'rchivagte des docuemnts de ce département
, il appel l'outil intégrer le service concerner utilise l'outil text updater, et une fois il estime qu'il peut publier , dans la gestion d'appel de cet outil il rendre dans le process d'approabtion en utilant le canaux wss que nous avons , l'utilisateur approve le text modifier est ajouté direcement firebase , l'utilisateur refuse, il recoit le refus de l'utilisateur et cloture sa tache 

donc factuellement il a combien d'outil
outil de recherche de context, apbookeeper, router, general_context ….ces outils fourjnissent uniqueemnt le context (3 outils)
outil de mise à jour de text avec comme arguments , le service concerné et les arugments qui propse l'outil ( definit selon uen regle de possition de text pour définir quel Zone modifié)
en dessous du capot, l'argument du service telecharge le contenu apporte les modifications demndés, presente les modifications a l'agent, l'agent approve ou refuse
approve, passe par l'approbation de l'utilisaeur, si le dernier approuve a lors les time stape 'last_refresh' sont rajouté et mise à jour dans firebase.
l'agent refuse la modfiication qui a l'a fait ,ces dernier modification sont annulés, et il peut modifier ou abondonner 
donc le dernier outil publish *********************************** A reflechir comment définir un fichier temporaire qui ne serait plus touché pour permettre de l'intégrer dans le process de publication avec les stamps last_refrecsh si approuvé par l'utilsiateur
Donc au total je compte 5 outils ah oui le 6 ieme, l'outil pour cloturer la tache avec le rapport succint de son activité.



