FROM amazonlinux:2

WORKDIR /tmp
RUN yum install python3 -y
RUN mkdir /build

WORKDIR /build