FROM python:3.10-alpine as builder
RUN apk update && apk add --update git gcc libc-dev libffi-dev
WORKDIR proxy
COPY ./requirements.txt ./
RUN pip3 install --target=/proxy/dependencies -r requirements.txt
COPY ./ ./

FROM python:3.10-alpine
WORKDIR mhddos_proxy
COPY --from=builder	/proxy .
ENV PYTHONPATH="${PYTHONPATH}:/proxy/dependencies" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python3", "./runner.py"]
